use std::{hint::black_box, time::Instant};

use qortoo::{Client, Counter, Datatype, LocalConnectivity};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Contract {
    schema_version: u32,
    id: String,
    transaction_operations: u64,
    max_counters_per_client: u64,
    execution: Execution,
    scenarios: Vec<Scenario>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Execution {
    realtime: bool,
    handler_registered: bool,
    measurement_threads: u32,
    warmup_rounds: u32,
    measured_rounds: u32,
    smoke_warmup_rounds: u32,
    smoke_rounds: u32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Scenario {
    id: String,
    budget: u64,
    smoke_budget: u64,
    reset_every: Option<u64>,
}

#[derive(Default, Serialize)]
struct Stats {
    counter_creations: u64,
    counter_releases: u64,
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
    value: i64,
}

#[derive(Serialize)]
struct Sample {
    schema_version: u32,
    contract_id: String,
    implementation: &'static str,
    profile: String,
    scenario: String,
    operations: u64,
    elapsed_ns: u128,
    ns_per_op: f64,
    stats: Stats,
    chunks: Vec<Chunk>,
}

fn new_client() -> Client {
    let connectivity = LocalConnectivity::new_arc();
    connectivity.set_realtime(false);
    Client::builder("bench-collection", "bench-counter")
        .with_connectivity(connectivity)
        .build()
        .expect("client must build")
}

fn new_counter(client: &Client, key: &str) -> Counter {
    client
        .create_datatype(key)
        .build_counter()
        .expect("counter must build")
}

fn run_round(contract: &Contract, scenario: &Scenario, profile: &str) -> Sample {
    let budget = match profile {
        "smoke" => scenario.smoke_budget,
        "full" => scenario.budget,
        _ => panic!("profile must be smoke or full"),
    };
    assert!(budget > 0);
    let client = new_client();
    let mut stats = Stats {
        client_creations: 1,
        ..Stats::default()
    };
    let mut chunks = Vec::new();
    let mut elapsed_ns = 0;

    match scenario.id.as_str() {
        "BuildCounter" => {
            assert!(budget <= contract.max_counters_per_client);
            let keys: Vec<_> = (0..budget).map(|i| format!("build-{i}")).collect();
            let start = Instant::now();
            for key in &keys {
                drop(black_box(new_counter(&client, key)));
            }
            elapsed_ns = start.elapsed().as_nanos();
            stats.counter_creations = budget;
            stats.counter_releases = budget;
        }
        "GetValue" => {
            let counter = new_counter(&client, "get-value");
            stats.counter_creations = 1;
            counter.increase().expect("seed must succeed");
            stats.setup_writes = 1;
            assert_eq!(counter.get_value(), 1);
            let mut value = 0;
            let start = Instant::now();
            for _ in 0..budget {
                value = black_box(black_box(&counter).get_value());
            }
            elapsed_ns = start.elapsed().as_nanos();
            assert_eq!(value, 1);
            assert_eq!(counter.get_value(), 1);
            stats.reads = budget;
            chunks.push(Chunk {
                operations: budget,
                value,
            });
            drop(counter);
            stats.counter_releases = 1;
        }
        "IncreaseBy" | "Transaction" | "SyncLocal" => {
            let reset = scenario.reset_every.expect("mutating scenario needs reset");
            assert!(reset > 0);
            let mut done = 0;
            while done < budget {
                let count = (budget - done).min(reset);
                let counter = new_counter(&client, &format!("counter-{done}"));
                stats.counter_creations += 1;
                let start = Instant::now();
                match scenario.id.as_str() {
                    "IncreaseBy" => {
                        for _ in 0..count {
                            black_box(
                                counter
                                    .increase_by(black_box(1))
                                    .expect("increase must succeed"),
                            );
                        }
                    }
                    "Transaction" => {
                        let transaction_operations = contract.transaction_operations;
                        for _ in 0..count {
                            counter
                                .transaction("bench", move |tx| {
                                    for _ in 0..transaction_operations {
                                        tx.increase_by(1)?;
                                    }
                                    Ok(())
                                })
                                .expect("transaction must commit");
                        }
                    }
                    "SyncLocal" => {
                        for _ in 0..count {
                            counter
                                .increase_by(black_box(1))
                                .expect("increase must succeed");
                            counter.sync().expect("sync must succeed");
                        }
                    }
                    _ => unreachable!(),
                }
                elapsed_ns += start.elapsed().as_nanos();
                let writes = count
                    * if scenario.id == "Transaction" {
                        contract.transaction_operations
                    } else {
                        1
                    };
                let value = counter.get_value();
                assert_eq!(value, i64::try_from(writes).expect("value must fit i64"));
                chunks.push(Chunk {
                    operations: count,
                    value,
                });
                stats.writes += writes;
                drop(counter);
                stats.counter_releases += 1;
                done += count;
            }
            stats.resets = stats.counter_creations - 1;
            if scenario.id == "Transaction" {
                stats.transactions = budget;
            }
            if scenario.id == "SyncLocal" {
                stats.syncs = budget;
            }
        }
        _ => panic!("unsupported scenario"),
    }
    // Logical releases are checked outside timing; they do not prove native reclamation.
    drop(client);
    stats.client_releases = 1;
    assert_eq!(stats.counter_creations, stats.counter_releases);
    Sample {
        schema_version: 1,
        contract_id: contract.id.clone(),
        implementation: "rust",
        profile: profile.to_owned(),
        scenario: scenario.id.clone(),
        operations: budget,
        elapsed_ns,
        ns_per_op: elapsed_ns as f64 / budget as f64,
        stats,
        chunks,
    }
}

fn main() {
    let args: Vec<_> = std::env::args().collect();
    assert_eq!(
        args.len(),
        4,
        "usage: counter <contract.json> <smoke|full> <scenario>"
    );
    let contract: Contract =
        serde_json::from_slice(&std::fs::read(&args[1]).expect("read contract"))
            .expect("parse contract");
    assert_eq!(contract.schema_version, 1);
    assert!(!contract.execution.realtime && !contract.execution.handler_registered);
    assert_eq!(contract.execution.measurement_threads, 1);
    assert!(contract.execution.measured_rounds > 0 && contract.execution.smoke_rounds > 0);
    // The caller schedules warmup/measured rounds; this process executes one round.
    let _ = (
        contract.execution.warmup_rounds,
        contract.execution.smoke_warmup_rounds,
    );
    assert!(contract.transaction_operations > 0);
    let scenario = contract
        .scenarios
        .iter()
        .find(|s| s.id == args[3])
        .expect("scenario must exist in contract");
    let sample = run_round(&contract, scenario, &args[2]);
    println!(
        "{}",
        serde_json::to_string(&sample).expect("serialize sample")
    );
}
