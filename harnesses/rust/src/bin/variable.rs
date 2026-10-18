use std::{hint::black_box, sync::Arc, time::Instant};

use qortoo::Datatype;
use qortoo_bench_sdk::{Execution, new_client};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Contract {
    schema_version: u32,
    id: String,
    transaction_operations: u64,
    max_payload_bytes_per_chunk: u64,
    execution: Execution,
    payloads: Vec<PayloadSpec>,
    scenarios: Vec<Scenario>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PayloadSpec {
    id: String,
    json_bytes: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Scenario {
    id: String,
    operation: String,
    payload: String,
    budget: u64,
    smoke_budget: u64,
    reset_every: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Eq, Deserialize, Serialize)]
struct Payload {
    sequence: String,
    data: String,
}

fn payload(index: u64, bytes: usize) -> Payload {
    assert!(index < 10_u64.pow(16), "sequence must keep its fixed width");
    let mut value = Payload {
        sequence: format!("{index:016}"),
        data: String::new(),
    };
    let overhead = serde_json::to_vec(&value).expect("encode fixture").len();
    assert!(bytes >= overhead);
    value.data = "x".repeat(bytes - overhead);
    assert_eq!(
        serde_json::to_vec(&value).expect("encode fixture").len(),
        bytes
    );
    value
}

#[derive(Default, Serialize)]
struct Stats {
    variable_creations: u64,
    variable_releases: u64,
    client_creations: u64,
    client_releases: u64,
    resets: u64,
    writes: u64,
    reads: u64,
    transactions: u64,
    syncs: u64,
    setup_writes: u64,
}

#[derive(Serialize)]
struct Chunk {
    operations: u64,
    value: Payload,
    previous_sequence: Option<String>,
}

#[derive(Serialize)]
struct Sample {
    schema_version: u32,
    contract_id: String,
    implementation: &'static str,
    profile: String,
    scenario: String,
    payload_id: String,
    payload_json_bytes: usize,
    operations: u64,
    elapsed_ns: u128,
    ns_per_op: f64,
    stats: Stats,
    chunks: Vec<Chunk>,
}

fn main() {
    let args: Vec<_> = std::env::args().collect();
    assert_eq!(
        args.len(),
        4,
        "usage: variable <contract.json> <smoke|full> <case>"
    );
    let contract: Contract =
        serde_json::from_slice(&std::fs::read(&args[1]).expect("read contract"))
            .expect("parse contract");
    assert_eq!(contract.schema_version, 1);
    assert!(!contract.execution.realtime && !contract.execution.handler_registered);
    assert_eq!(contract.execution.measurement_threads, 1);
    assert!(contract.execution.measured_rounds > 0 && contract.execution.smoke_rounds > 0);
    assert!(contract.transaction_operations > 0);
    let scenario = contract
        .scenarios
        .iter()
        .find(|s| s.id == args[3])
        .expect("case must exist");
    let spec = contract
        .payloads
        .iter()
        .find(|p| p.id == scenario.payload)
        .expect("payload must exist");
    let budget = match args[2].as_str() {
        "smoke" => scenario.smoke_budget,
        "full" => scenario.budget,
        _ => panic!("profile must be smoke or full"),
    };
    assert!(budget > 0);
    let read = scenario.operation == "Get";
    let reset = if read {
        budget
    } else {
        scenario.reset_every.expect("missing reset")
    };
    assert!(reset > 0);
    let client = new_client();
    let mut stats = Stats {
        client_creations: 1,
        ..Stats::default()
    };
    let mut chunks = Vec::new();
    let mut elapsed_ns = 0;
    let mut done = 0;
    while done < budget {
        let count = (budget - done).min(reset);
        let writes = if read {
            0
        } else {
            count
                * if scenario.operation == "Transaction" {
                    contract.transaction_operations
                } else {
                    1
                }
        };
        assert!((writes + 1) * spec.json_bytes as u64 <= contract.max_payload_bytes_per_chunk);
        let values: Arc<Vec<_>> =
            Arc::new((0..=writes).map(|i| payload(i, spec.json_bytes)).collect());
        let variable = client
            .create_datatype(format!("variable-{done}"))
            .build_variable()
            .expect("variable must build");
        assert!(
            variable
                .set(&values[0])
                .expect("seed must succeed")
                .is_null()
        );
        stats.variable_creations += 1;
        stats.setup_writes += 1;
        let mut last_read = None;
        let mut previous = serde_json::Value::Null;
        let start = Instant::now();
        match scenario.operation.as_str() {
            "Get" => {
                for _ in 0..count {
                    last_read = Some(black_box(
                        variable.get::<Payload>().expect("get must succeed"),
                    ));
                }
            }
            "Set" | "SyncLocal" => {
                for value in &values[1..] {
                    previous = black_box(variable.set(black_box(value)).expect("set must succeed"));
                    if scenario.operation == "SyncLocal" {
                        variable.sync().expect("sync must succeed");
                    }
                }
            }
            "Transaction" => {
                let operations = contract.transaction_operations as usize;
                for transaction in 0..count as usize {
                    let owned = Arc::clone(&values);
                    variable
                        .transaction("bench", move |tx| {
                            for value in &owned
                                [transaction * operations + 1..=(transaction + 1) * operations]
                            {
                                tx.set(value)?;
                            }
                            Ok(())
                        })
                        .expect("transaction must commit");
                }
            }
            _ => panic!("unsupported variable operation"),
        }
        elapsed_ns += start.elapsed().as_nanos();
        let value = variable
            .get::<Payload>()
            .expect("validation get must succeed");
        assert_eq!(&value, values.last().expect("seed exists"));
        if read {
            assert_eq!(last_read.as_ref(), Some(&values[0]));
        }
        let previous_sequence = if matches!(scenario.operation.as_str(), "Set" | "SyncLocal") {
            let previous: Payload =
                serde_json::from_value(previous).expect("previous value must decode");
            assert_eq!(previous, values[values.len() - 2]);
            Some(previous.sequence)
        } else {
            None
        };
        chunks.push(Chunk {
            operations: count,
            value,
            previous_sequence,
        });
        stats.writes += writes;
        if read {
            stats.reads += count;
        }
        if scenario.operation == "Transaction" {
            stats.transactions += count;
        }
        if scenario.operation == "SyncLocal" {
            stats.syncs += count;
        }
        drop(variable);
        stats.variable_releases += 1;
        done += count;
    }
    stats.resets = stats.variable_creations - 1;
    drop(client);
    stats.client_releases = 1;
    assert_eq!(stats.variable_creations, stats.variable_releases);
    let sample = Sample {
        schema_version: 1,
        contract_id: contract.id.clone(),
        implementation: "rust",
        profile: args[2].clone(),
        scenario: scenario.id.clone(),
        payload_id: spec.id.clone(),
        payload_json_bytes: spec.json_bytes,
        operations: budget,
        elapsed_ns,
        ns_per_op: elapsed_ns as f64 / budget as f64,
        stats,
        chunks,
    };
    println!(
        "{}",
        serde_json::to_string(&sample).expect("serialize sample")
    );
}
