module github.com/qortoo/qortoo-bench/harnesses/go

go 1.25.0

require github.com/qortoo/qortoo-go v0.0.0

require (
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	go.opentelemetry.io/otel v1.45.0 // indirect
	go.opentelemetry.io/otel/trace v1.45.0 // indirect
)

replace github.com/qortoo/qortoo-go => ../../_sources/go
