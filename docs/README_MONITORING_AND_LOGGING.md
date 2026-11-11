# Monitoring & Logging Readme

## Grafana

This setup uses Grafana for displaying and analyzing the captured metrics and logs via dashboards.

You can view the grafana dashboard via the following kmf command:
```
ENVIRONMENT="dev"
uv run kmt tunnel grafana $ENVIRONMENT

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

## Logging (Loki + Alloy)
This setup uses Loki for log aggregation and Grafana to access the stored logs. Loki arrives as one
release (`grafana-community/loki`, monolithic, single replica, filesystem-backed, 14-day retention)
and the collector arrives as a second one (`grafana/alloy` as a DaemonSet), which is what reads every
container's log files off each node plus the cluster's Kubernetes events. Both live in the `loki`
namespace and both are swept when it is deleted.

- Official documentation: https://grafana.com/docs/loki/latest/
- Loki Query Language documentation: https://grafana.com/docs/loki/latest/logql/
- Alloy documentation: https://grafana.com/docs/alloy/latest/

The labels every log line carries are `namespace`, `pod`, `container`, `app`, `job`, `instance`,
`component`, `node_name` and `stream` (the last of those is `stdout` or `stderr`). They are deliberately
the ones the retired `loki-stack`/Promtail bundle produced, so queries written against an older cluster
keep answering.

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
{namespace="dev1", app="nginx"}
```

(The label is `app` and not `app_kubernetes_io_name`, because the collector copies
`app.kubernetes.io/name` onto the stream rather than passing the label key through. That is the
`loki-stack` convention, kept on purpose so old queries keep working.)