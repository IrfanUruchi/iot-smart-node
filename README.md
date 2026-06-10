# IoT Smart Node

IoT Smart Node is the node-side runtime agent for the Intelligent Fog Orchestration System.

It runs on an IoT, edge, or fog device and is responsible for executing the workloads assigned by the Fog Controller. The node connects to the MQTT broker, receives desired-state updates, manages local Docker containers, publishes telemetry, reports workload status, and performs reconciliation when the actual runtime state does not match the expected state.

The node is designed to run on local machines, Linux devices, Proxmox-hosted environments, and remote machines connected through Tailscale.

## Runtime Model

The IoT Smart Node follows a desired-state execution model.

The Fog Controller defines which workloads should run on a node. The node receives that state through MQTT and compares it with the local Docker runtime. If a workload is missing, the node starts it. If a workload needs to be stopped, the node stops and removes the managed container.

This keeps the system distributed. The controller decides the expected state, while each node manages its own local execution environment.

## Docker Image

```text
irfanuruchi/iot-smart-node:latest
```

The image is published for:

```text
linux/amd64
linux/arm64
```

## Configuration

The node can be configured through `config.yaml` and environment variables.

For local Docker Desktop development:

```bash
MQTT_HOST=host.docker.internal
MQTT_PORT=1883
```

For same-network deployment:

```bash
MQTT_HOST=192.168.x.x
MQTT_PORT=1883
```

For remote deployment through Tailscale:

```bash
MQTT_HOST=100.x.x.x
MQTT_PORT=1883
```

Each node needs a unique node ID:

```bash
NODE_ID=node-1
```

or:

```bash
NODE_ID=node-2
```

The node also uses a shared device token for protected commands:

```bash
DEVICE_TOKEN=changeme
```

## Run

Node 1 example:

```bash
docker run -d \
  --name node-1 \
  -e NODE_ID=node-1 \
  -e MQTT_HOST=host.docker.internal \
  -e MQTT_PORT=1883 \
  -e DEVICE_TOKEN=changeme \
  -v /var/run/docker.sock:/var/run/docker.sock \
  irfanuruchi/iot-smart-node:latest
```

Node 2 example with Cerebras key for the Integral Calculator workload:

```bash
docker run -d \
  --name node-2 \
  -e NODE_ID=node-2 \
  -e MQTT_HOST=host.docker.internal \
  -e MQTT_PORT=1883 \
  -e DEVICE_TOKEN=changeme \
  -e CEREBRAS_API_KEY=$CEREBRAS_API_KEY \
  -v /var/run/docker.sock:/var/run/docker.sock \
  irfanuruchi/iot-smart-node:latest
```

The Docker socket mount is required because the IoT Smart Node manages the host Docker runtime directly:

```bash
-v /var/run/docker.sock:/var/run/docker.sock
```

## Services

The node reads allowed workloads from `config.yaml`.

Example services include ProMatch, FluidLab CFD, Integral Calculator, and CNN Edge Classifier.

```yaml
services:
  integral-calculator:
    image: "irfanuruchi/integral-calculator:latest"
    ports:
      - "5050:3000"
    cpu_limit: 0.5
    mem_limit: "256m"
    read_only: false
    env:
      - "CEREBRAS_API_KEY=${CEREBRAS_API_KEY}"

  promatch:
    image: "irfanuruchi/promatch:latest"
    ports:
      - "3000:3000"
    cpu_limit: 1.0
    mem_limit: "512m"
    read_only: false

  fluidlab:
    image: "irfanuruchi/fluid-lab-cfd:cpu"
    ports:
      - "8082:8000"
    cpu_limit: 1.0
    mem_limit: "1g"
    shm_size: "2g"
    read_only: false
    env:
      - "MPLCONFIGDIR=/tmp/matplotlib"

  cnn-edge-classifier:
    image: "irfanuruchi/cnn-edge-classifier:latest"
    ports:
      - "8600:8600"
    cpu_limit: 1.0
    mem_limit: "1g"
    read_only: false
```

## Desired-State Example

Node 1 can be assigned heavier edge workloads:

```text
node-1:
  - promatch
  - fluidlab
```

Node 2 can be assigned symbolic computation and CNN inference workloads:

```text
node-2:
  - integral-calculator
  - cnn-edge-classifier
```

After receiving the desired state, the IoT Smart Node deploys the required Docker containers and publishes status updates back to the controller.

## Workload Access

When running locally, deployed workloads are usually available at:

```text
ProMatch:              http://localhost:3000
FluidLab CFD:          http://localhost:8082
Integral Calculator:   http://localhost:5050
CNN Edge Classifier:   http://localhost:8600/ui
```

In distributed deployments, replace `localhost` with the LAN IP address or Tailscale IP address of the node running the workload.

## Security and Runtime Controls

The node supports token-protected command handling. Commands received through MQTT must include the configured device token before deployment, stop, status, or runtime configuration actions are accepted.

The node also includes rate limiting and burst limiting to avoid command spam. Runtime configuration updates can be restricted to selected configuration paths, such as service definitions and resource limits.

The node treats Docker access as a trusted local runtime capability. Since it mounts the Docker socket, it can start, stop, and inspect containers on the host machine. This is required for the orchestration behavior of the project.

## Telemetry and Monitoring

The node publishes heartbeat and status messages through MQTT. These messages include node identity, metrics, running services, allowed services, and runtime state.

The controller uses these updates to display node status, workload state, and orchestration events.

## Reliability

The IoT Smart Node includes self-healing behavior for managed containers.

If a managed container stops unexpectedly and self-healing is enabled, the node can restart it and publish an alert. This supports the desired-state model by keeping the actual runtime state close to the expected state.

## Related Repositories

```text
https://github.com/IrfanUruchi/intelligent-fog-orchestration-system
https://github.com/IrfanUruchi/fog-controller
https://github.com/IrfanUruchi/fog-intelligence-llm
https://github.com/IrfanUruchi/cnn-edge-classifier
```

## Author

Irfan Uruçi  
South East European University  
Intelligent Systems Course Project  
Academic Year 2026
