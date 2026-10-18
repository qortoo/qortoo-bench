package main

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
)

type execution struct {
	Realtime           bool `json:"realtime"`
	HandlerRegistered  bool `json:"handler_registered"`
	MeasurementThreads int  `json:"measurement_threads"`
	WarmupRounds       int  `json:"warmup_rounds"`
	MeasuredRounds     int  `json:"measured_rounds"`
	SmokeWarmupRounds  int  `json:"smoke_warmup_rounds"`
	SmokeRounds        int  `json:"smoke_rounds"`
}

func require(ok bool, message string) {
	if !ok {
		panic(message)
	}
}

func readContractJSON(path string, destination any) {
	data, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		panic(err)
	}
	var extra any
	require(decoder.Decode(&extra) == io.EOF, "contract contains trailing data")
}
