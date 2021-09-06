# Monitoring & Logging Readme

## Grafana

This setup uses Grafana for displaying and analyzing the captured metrics and logs via dashboards.

You can view the grafana dashboard via the following commands:
```
cd utilities
./open_grafana_tunnel.sh

# Access http://localhost:3000/ via your browser and login
# Credentials are stored in the GRAFANA_ADMIN_USER and GRAFANA_ADMIN_PASSWORD configuration variables in platform_config/${ENVIRONMENT}/static.encrypted.json
# e.g. cat platform_config/dev/static.encrypted.json | grep "GRAFANA_"
```

## Monitoring (Prometheus)
This setup uses Prometheus for collecting logs from all nodes and running components.

- Official documentation: https://prometheus.io/docs/introduction/overview/
- Prometheus Query Language documentation: https://prometheus.io/docs/prometheus/latest/querying/basics/

### Metric retrieval examples
You can navigate to the grafana "Explore" view and select Prometheus as datasource for your queries.
Here are some basic examples for metric querying:

```
#
container_cpu_usage_seconds_total{namespace="dev1",pod=~"exampleapp.*"}

#
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="dev1",pod=~"exampleapp.*"}[2m]))
```

## Logging (Loki)
This setup uses Loki for log aggregation and grafana to access the stored logs.

- Official documentation: https://grafana.com/docs/loki/latest/
- Loki Query Language documentation: https://grafana.com/docs/loki/latest/logql/

### Log retrieval examples
You can navigate to the grafana "Explore" view and select Loki as datasource for your queries.
Here are some basic examples for log querying:

```
# select all logs from namespace dev1-pipelines
{namespace="dev1-pipelines"}

# select all logs from namespace dev1 with log lines containing error
{namespace="dev1"} |= "error"

# select all logs in namespace dev1-pipelines with log lines containing example*
{namespace="dev1-pipelines"} |~ "example.*"

# select all logs in namespace dev1 for helm deployment nginx
{namespace="dev1", app_kubernetes_io_name="nginx"}
```