package main

import "testing"

// Exact-boundary and boundary-plus-one rounds must release different numbers of
// counters while preserving the workload's final value on the last counter.
func TestResetBoundary(t *testing.T) {
	c := loadContract("../../contracts/counter.json")
	for _, s := range c.Scenarios {
		if s.ResetEvery == 0 {
			continue
		}
		for _, extra := range []uint64{0, 1} {
			t.Run(s.ID+"/"+map[uint64]string{0: "exact", 1: "after"}[extra], func(t *testing.T) {
				s.SmokeBudget = s.ResetEvery + extra
				r := runRound(c, s, "smoke")
				if r.Stats.CounterCreations != 1+extra || r.Stats.CounterReleases != 1+extra || r.Stats.Resets != extra {
					t.Fatalf("unexpected reset/release accounting: %+v", r.Stats)
				}
				writes := s.ResetEvery
				if extra == 1 {
					writes = 1
				}
				if s.ID == "Transaction" {
					writes *= c.TransactionOperations
				}
				last := r.Chunks[len(r.Chunks)-1]
				if last.Value != int64(writes) || r.Stats.ClientReleases != 1 {
					t.Fatalf("unexpected final chunk or client release: %+v", r)
				}
			})
		}
	}
}
