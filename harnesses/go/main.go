package main

import (
	"encoding/json"
	"os"
	"strings"
)

func main() {
	require(len(os.Args) == 4, "usage: sdk <contract.json> <smoke|full> <scenario>")
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	var identity struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(data, &identity); err != nil {
		panic(err)
	}
	if strings.HasPrefix(identity.ID, "variable/") {
		c := loadVariableContract(os.Args[1])
		for _, s := range c.Scenarios {
			if s.ID == os.Args[3] {
				if err := json.NewEncoder(os.Stdout).Encode(runVariableRound(c, s, os.Args[2])); err != nil {
					panic(err)
				}
				return
			}
		}
		panic("scenario must exist in contract")
	}
	c := loadCounterContract(os.Args[1])
	for _, s := range c.Scenarios {
		if s.ID == os.Args[3] {
			if err := json.NewEncoder(os.Stdout).Encode(runCounterRound(c, s, os.Args[2])); err != nil {
				panic(err)
			}
			return
		}
	}
	panic("scenario must exist in contract")
}
