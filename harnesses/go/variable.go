package main

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	qortoo "github.com/qortoo/qortoo-go"
)

type variablePayloadSpec struct {
	ID        string `json:"id"`
	JSONBytes uint64 `json:"json_bytes"`
}

type variableScenario struct {
	ID          string `json:"id"`
	Operation   string `json:"operation"`
	Payload     string `json:"payload"`
	Budget      uint64 `json:"budget"`
	SmokeBudget uint64 `json:"smoke_budget"`
	ResetEvery  uint64 `json:"reset_every"`
}

type variableContract struct {
	SchemaVersion           int                   `json:"schema_version"`
	ID                      string                `json:"id"`
	TransactionOperations   uint64                `json:"transaction_operations"`
	MaxPayloadBytesPerChunk uint64                `json:"max_payload_bytes_per_chunk"`
	Execution               execution             `json:"execution"`
	Payloads                []variablePayloadSpec `json:"payloads"`
	Scenarios               []variableScenario    `json:"scenarios"`
}

type variablePayload struct {
	Sequence string `json:"sequence"`
	Data     string `json:"data"`
}

func makePayload(index, size uint64) variablePayload {
	require(index < 10000000000000000, "sequence must keep its fixed width")
	value := variablePayload{Sequence: fmt.Sprintf("%016d", index)}
	encoded, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	require(size >= uint64(len(encoded)), "payload is too small")
	value.Data = strings.Repeat("x", int(size)-len(encoded))
	encoded, err = json.Marshal(value)
	if err != nil {
		panic(err)
	}
	require(uint64(len(encoded)) == size, "payload byte count mismatch")
	return value
}

type variableStats struct {
	VariableCreations uint64 `json:"variable_creations"`
	VariableReleases  uint64 `json:"variable_releases"`
	ClientCreations   uint64 `json:"client_creations"`
	ClientReleases    uint64 `json:"client_releases"`
	Resets            uint64 `json:"resets"`
	Writes            uint64 `json:"writes"`
	Reads             uint64 `json:"reads"`
	Transactions      uint64 `json:"transactions"`
	Syncs             uint64 `json:"syncs"`
	SetupWrites       uint64 `json:"setup_writes"`
}

type variableChunk struct {
	Operations       uint64          `json:"operations"`
	Value            variablePayload `json:"value"`
	PreviousSequence *string         `json:"previous_sequence"`
}

type variableSample struct {
	SchemaVersion    int             `json:"schema_version"`
	ContractID       string          `json:"contract_id"`
	Implementation   string          `json:"implementation"`
	Profile          string          `json:"profile"`
	Scenario         string          `json:"scenario"`
	PayloadID        string          `json:"payload_id"`
	PayloadJSONBytes uint64          `json:"payload_json_bytes"`
	Operations       uint64          `json:"operations"`
	ElapsedNS        int64           `json:"elapsed_ns"`
	NSPerOp          float64         `json:"ns_per_op"`
	Stats            variableStats   `json:"stats"`
	Chunks           []variableChunk `json:"chunks"`
}

func loadVariableContract(path string) variableContract {
	var c variableContract
	readContractJSON(path, &c)
	require(c.SchemaVersion == 1, "unsupported contract schema")
	require(!c.Execution.Realtime && !c.Execution.HandlerRegistered, "unsupported execution conditions")
	require(c.Execution.MeasurementThreads == 1, "measurement must be single-threaded")
	require(c.Execution.MeasuredRounds > 0 && c.Execution.SmokeRounds > 0, "round counts must be positive")
	require(c.TransactionOperations > 0, "transaction size must be positive")
	return c
}

func runVariableRound(c variableContract, s variableScenario, profile string) variableSample {
	budget := s.Budget
	switch profile {
	case "smoke":
		budget = s.SmokeBudget
	case "full":
	default:
		panic("profile must be smoke or full")
	}
	require(budget > 0, "budget must be positive")
	var spec variablePayloadSpec
	for _, p := range c.Payloads {
		if p.ID == s.Payload {
			spec = p
		}
	}
	require(spec.JSONBytes > 0, "payload must exist")
	read := s.Operation == "Get"
	reset := s.ResetEvery
	if read {
		reset = budget
	}
	require(reset > 0, "missing reset")
	bc := newClient()
	defer bc.close()
	result := variableSample{
		SchemaVersion: 1, ContractID: c.ID, Implementation: "go", Profile: profile,
		Scenario: s.ID, PayloadID: spec.ID, PayloadJSONBytes: spec.JSONBytes,
		Operations: budget, Stats: variableStats{ClientCreations: 1}, Chunks: []variableChunk{},
	}
	for done := uint64(0); done < budget; {
		count := min(budget-done, reset)
		writes := count
		if read {
			writes = 0
		} else if s.Operation == "Transaction" {
			writes *= c.TransactionOperations
		}
		require((writes+1)*spec.JSONBytes <= c.MaxPayloadBytesPerChunk, "payload chunk exceeds envelope")
		values := make([]variablePayload, writes+1)
		for i := range values {
			values[i] = makePayload(uint64(i), spec.JSONBytes)
		}
		func() {
			v, err := bc.client.CreateVariable(fmt.Sprintf("variable-%d", done), nil)
			if err != nil {
				panic(err)
			}
			defer v.Close()
			previous, err := v.Set(values[0])
			if err != nil {
				panic(err)
			}
			require(previous == nil, "new variable must start at null")
			result.Stats.VariableCreations++
			result.Stats.SetupWrites++
			var lastRead variablePayload
			start := time.Now()
			switch s.Operation {
			case "Get":
				for i := uint64(0); i < count; i++ {
					var value variablePayload
					if err := v.Get(&value); err != nil {
						panic(err)
					}
					lastRead = value
				}
			case "Set", "SyncLocal":
				for _, value := range values[1:] {
					previous, err = v.Set(value)
					if err != nil {
						panic(err)
					}
					if s.Operation == "SyncLocal" {
						if err := v.Sync(); err != nil {
							panic(err)
						}
					}
				}
			case "Transaction":
				for i := uint64(0); i < count; i++ {
					batch := values[i*c.TransactionOperations+1 : (i+1)*c.TransactionOperations+1]
					if err := v.Transaction("bench", func(tx *qortoo.Variable) error {
						for _, value := range batch {
							if _, err := tx.Set(value); err != nil {
								return err
							}
						}
						return nil
					}); err != nil {
						panic(err)
					}
				}
			default:
				panic("unsupported variable operation")
			}
			result.ElapsedNS += time.Since(start).Nanoseconds()
			var value variablePayload
			if err := v.Get(&value); err != nil {
				panic(err)
			}
			require(value == values[len(values)-1], "unexpected final payload")
			if read {
				require(lastRead == values[0], "Get changed the seeded value")
			}
			var previousSequence *string
			if s.Operation == "Set" || s.Operation == "SyncLocal" {
				encoded, err := json.Marshal(previous)
				if err != nil {
					panic(err)
				}
				var prior variablePayload
				if err := json.Unmarshal(encoded, &prior); err != nil {
					panic(err)
				}
				require(prior == values[len(values)-2], "unexpected previous payload")
				previousSequence = &prior.Sequence
			}
			result.Chunks = append(result.Chunks, variableChunk{Operations: count, Value: value, PreviousSequence: previousSequence})
		}()
		result.Stats.VariableReleases++
		result.Stats.Writes += writes
		if read {
			result.Stats.Reads += count
		}
		if s.Operation == "Transaction" {
			result.Stats.Transactions += count
		}
		if s.Operation == "SyncLocal" {
			result.Stats.Syncs += count
		}
		done += count
	}
	result.Stats.Resets = result.Stats.VariableCreations - 1
	result.Stats.ClientReleases = 1
	require(result.Stats.VariableCreations == result.Stats.VariableReleases, "unreleased variable")
	result.NSPerOp = float64(result.ElapsedNS) / float64(budget)
	return result
}
