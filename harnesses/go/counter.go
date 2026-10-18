package main

import (
	"fmt"
	"time"

	qortoo "github.com/qortoo/qortoo-go"
)

type counterScenario struct {
	ID          string `json:"id"`
	Budget      uint64 `json:"budget"`
	SmokeBudget uint64 `json:"smoke_budget"`
	ResetEvery  uint64 `json:"reset_every"`
}

type counterContract struct {
	SchemaVersion         int               `json:"schema_version"`
	ID                    string            `json:"id"`
	TransactionOperations uint64            `json:"transaction_operations"`
	MaxCountersPerClient  uint64            `json:"max_counters_per_client"`
	Execution             execution         `json:"execution"`
	Scenarios             []counterScenario `json:"scenarios"`
}

type counterStats struct {
	CounterCreations uint64 `json:"counter_creations"`
	CounterReleases  uint64 `json:"counter_releases"`
	ClientCreations  uint64 `json:"client_creations"`
	ClientReleases   uint64 `json:"client_releases"`
	Resets           uint64 `json:"resets"`
	Writes           uint64 `json:"writes"`
	Reads            uint64 `json:"reads"`
	Transactions     uint64 `json:"transactions"`
	Syncs            uint64 `json:"syncs"`
	SetupWrites      uint64 `json:"setup_writes"`
}

type counterChunk struct {
	Operations uint64 `json:"operations"`
	Value      int64  `json:"value"`
}

type counterSample struct {
	SchemaVersion  int            `json:"schema_version"`
	ContractID     string         `json:"contract_id"`
	Implementation string         `json:"implementation"`
	Profile        string         `json:"profile"`
	Scenario       string         `json:"scenario"`
	Operations     uint64         `json:"operations"`
	ElapsedNS      int64          `json:"elapsed_ns"`
	NSPerOp        float64        `json:"ns_per_op"`
	Stats          counterStats   `json:"stats"`
	Chunks         []counterChunk `json:"chunks"`
}

func newCounter(client *qortoo.Client, key string) *qortoo.Counter {
	counter, err := client.CreateCounter(key, nil)
	if err != nil {
		panic(err)
	}
	return counter
}

func runCounterRound(c counterContract, s counterScenario, profile string) counterSample {
	budget := s.Budget
	switch profile {
	case "smoke":
		budget = s.SmokeBudget
	case "full":
	default:
		panic("profile must be smoke or full")
	}
	require(budget > 0, "budget must be positive")
	bc := newClient()
	defer bc.close()
	result := counterSample{
		SchemaVersion: 1, ContractID: c.ID, Implementation: "go", Profile: profile,
		Scenario: s.ID, Operations: budget, Chunks: []counterChunk{},
		Stats: counterStats{ClientCreations: 1},
	}
	switch s.ID {
	case "BuildCounter":
		require(budget <= c.MaxCountersPerClient, "BuildCounter budget exceeds client capacity")
		keys := make([]string, budget)
		for i := range keys {
			keys[i] = fmt.Sprintf("build-%d", i)
		}
		start := time.Now()
		for _, key := range keys {
			counter := newCounter(bc.client, key)
			counter.Close()
		}
		result.ElapsedNS = time.Since(start).Nanoseconds()
		result.Stats.CounterCreations = budget
		result.Stats.CounterReleases = budget
	case "GetValue":
		func() {
			counter := newCounter(bc.client, "get-value")
			defer counter.Close()
			result.Stats.CounterCreations = 1
			if _, err := counter.Increase(); err != nil {
				panic(err)
			}
			result.Stats.SetupWrites = 1
			require(counter.Value() == 1, "GetValue seed must be 1")
			var value int64
			start := time.Now()
			for i := uint64(0); i < budget; i++ {
				value = counter.Value()
			}
			result.ElapsedNS = time.Since(start).Nanoseconds()
			require(value == 1 && counter.Value() == 1, "GetValue changed the seeded value")
			result.Stats.Reads = budget
			result.Chunks = append(result.Chunks, counterChunk{Operations: budget, Value: value})
		}()
		result.Stats.CounterReleases = 1
	case "IncreaseBy", "Transaction", "SyncLocal":
		require(s.ResetEvery > 0, "mutating scenario needs reset")
		for done := uint64(0); done < budget; {
			count := min(budget-done, s.ResetEvery)
			func() {
				counter := newCounter(bc.client, fmt.Sprintf("counter-%d", done))
				defer counter.Close()
				result.Stats.CounterCreations++
				start := time.Now()
				switch s.ID {
				case "IncreaseBy":
					for i := uint64(0); i < count; i++ {
						if _, err := counter.IncreaseBy(1); err != nil {
							panic(err)
						}
					}
				case "Transaction":
					for i := uint64(0); i < count; i++ {
						if err := counter.Transaction("bench", func(tx *qortoo.Counter) error {
							for j := uint64(0); j < c.TransactionOperations; j++ {
								if _, err := tx.IncreaseBy(1); err != nil {
									return err
								}
							}
							return nil
						}); err != nil {
							panic(err)
						}
					}
				case "SyncLocal":
					for i := uint64(0); i < count; i++ {
						if _, err := counter.IncreaseBy(1); err != nil {
							panic(err)
						}
						if err := counter.Sync(); err != nil {
							panic(err)
						}
					}
				}
				result.ElapsedNS += time.Since(start).Nanoseconds()
				writes := count
				if s.ID == "Transaction" {
					writes *= c.TransactionOperations
				}
				value := counter.Value()
				require(value == int64(writes), "counter chunk has an unexpected final value")
				result.Chunks = append(result.Chunks, counterChunk{Operations: count, Value: value})
				result.Stats.Writes += writes
			}()
			result.Stats.CounterReleases++
			done += count
		}
		result.Stats.Resets = result.Stats.CounterCreations - 1
		if s.ID == "Transaction" {
			result.Stats.Transactions = budget
		}
		if s.ID == "SyncLocal" {
			result.Stats.Syncs = budget
		}
	default:
		panic("unsupported scenario")
	}
	// The deferred close executes before the caller receives this sample.
	result.Stats.ClientReleases = 1
	require(result.Stats.CounterCreations == result.Stats.CounterReleases, "unreleased counter")
	result.NSPerOp = float64(result.ElapsedNS) / float64(budget)
	return result
}

func loadCounterContract(path string) counterContract {
	var c counterContract
	readContractJSON(path, &c)
	require(c.SchemaVersion == 1, "unsupported contract schema")
	require(!c.Execution.Realtime && !c.Execution.HandlerRegistered, "unsupported execution conditions")
	require(c.Execution.MeasurementThreads == 1, "measurement must be single-threaded")
	require(c.Execution.MeasuredRounds > 0 && c.Execution.SmokeRounds > 0, "round counts must be positive")
	require(c.TransactionOperations > 0, "transaction size must be positive")
	return c
}
