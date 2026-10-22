FROM golang:1.27.2-bookworm AS go
FROM rust:1.97.1-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* && mkdir -p /work /results && chmod 777 /work /results
COPY --from=go /usr/local/go /usr/local/go
COPY . /opt/harness
ENV PATH=/usr/local/go/bin:/usr/local/cargo/bin:$PATH \
    CARGO_HOME=/work/cargo RUSTUP_HOME=/usr/local/rustup HOME=/work/home \
    GOTOOLCHAIN=local ImageOS=candidate-container TMPDIR=/work/tmp
USER 1000:1000
WORKDIR /work
CMD ["sleep", "infinity"]
