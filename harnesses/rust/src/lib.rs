use qortoo::{Client, LocalConnectivity};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Execution {
    pub realtime: bool,
    pub handler_registered: bool,
    pub measurement_threads: u32,
    pub warmup_rounds: u32,
    pub measured_rounds: u32,
    pub smoke_warmup_rounds: u32,
    pub smoke_rounds: u32,
}

pub fn new_client() -> Client {
    let connectivity = LocalConnectivity::new_arc();
    connectivity.set_realtime(false);
    Client::builder("bench-collection", "bench-counter")
        .with_connectivity(connectivity)
        .build()
        .expect("client must build")
}
