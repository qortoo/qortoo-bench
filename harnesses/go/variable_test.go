package main

import (
	"encoding/json"
	"fmt"
	"testing"
)

func TestVariablePayloadBytes(t *testing.T) {
	c := loadVariableContract("../../contracts/variable.json")
	for _, spec := range c.Payloads {
		for _, sequence := range []uint64{0, 9, 10, 1001} {
			value := makePayload(sequence, spec.JSONBytes)
			encoded, err := json.Marshal(value)
			if err != nil || uint64(len(encoded)) != spec.JSONBytes {
				t.Fatalf("payload %s/%d has wrong JSON size: %d, %v", spec.ID, sequence, len(encoded), err)
			}
		}
	}
}

func TestVariableResetBoundary(t *testing.T) {
	c := loadVariableContract("../../contracts/variable.json")
	for _, s := range c.Scenarios {
		if s.ResetEvery == 0 {
			continue
		}
		for _, extra := range []uint64{0, 1} {
			t.Run(fmt.Sprintf("%s/extra=%d", s.ID, extra), func(t *testing.T) {
				s.SmokeBudget = s.ResetEvery + extra
				r := runVariableRound(c, s, "smoke")
				if r.Stats.VariableCreations != 1+extra || r.Stats.VariableReleases != 1+extra || r.Stats.Resets != extra {
					t.Fatalf("wrong reset/release accounting: %+v", r.Stats)
				}
				writes := s.ResetEvery
				if extra == 1 {
					writes = 1
				}
				if s.Operation == "Transaction" {
					writes *= c.TransactionOperations
				}
				if r.Chunks[len(r.Chunks)-1].Value.Sequence != fmt.Sprintf("%016d", writes) {
					t.Fatal("wrong final sequence after reset")
				}
			})
		}
	}
}
