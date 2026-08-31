FROM ubuntu:24.04

ARG RUNNER_VERSION="2.331.0"
ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive

RUN useradd -m -s /bin/bash runner

RUN apt-get update && apt-get install -y \
    curl \
    git \
    jq \
    python3 \
    sudo \
    libicu-dev \
    libkrb5-3 \
    zlib1g \
    iputils-ping \
    openssh-client \
    build-essential \
    libopenblas-dev \
    libpq-dev \
    cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /home/runner

# TARGETARCH is set by buildx (amd64/arm64) so one Dockerfile covers any host --
# no assumption baked in about the fleet running on a particular chip.
RUN case "${TARGETARCH}" in \
      amd64) RUNNER_ARCH=x64 ;; \
      arm64) RUNNER_ARCH=arm64 ;; \
      *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && mkdir -p /home/runner/actions-runner && cd /home/runner/actions-runner \
    && curl -o runner.tar.gz -L \
       "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz" \
    && tar xzf runner.tar.gz \
    && rm runner.tar.gz

RUN cd /home/runner/actions-runner && ./bin/installdependencies.sh

COPY start.sh /home/runner/start.sh
RUN chown runner:runner /home/runner/start.sh && chmod +x /home/runner/start.sh

USER runner

ENTRYPOINT ["/home/runner/start.sh"]
