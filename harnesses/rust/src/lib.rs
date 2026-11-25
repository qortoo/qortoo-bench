use qortoo::{Client, ClientError, LocalConnectivity};
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
    let client = Client::builder("bench-collection", "bench-counter")
        .with_connectivity(connectivity)
        .build()
        .expect("client must build");
    client.open().expect("client must open");
    client
}

// Candidate comparisons also build against SDK revisions whose build() returns a ready client.
// On newer SDKs, the inherent Client::open method takes precedence over this fallback.
#[allow(dead_code, reason = "used only with SDK revisions before Client::open")]
trait LegacyClientOpen {
    fn open(&self) -> Result<(), ClientError> {
        Ok(())
    }
}

impl LegacyClientOpen for Client {}
