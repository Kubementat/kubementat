# Smart Telematics Web UI Helm Chart

This chart is used for deploying a demo web service as an example for implementing custom helm charts into
kubementat automation. Routing belongs to the platform, not to the chart: kubementat installs Traefik as the
cluster's default IngressClass, so this example installs no controller of its own and states which class
answers for its Ingress.

## Prerequisites

### Install helm
https://helm.sh/docs/intro/install/

### define namespace for deployment
```
# define target namespace
NAMESPACE="dev1"
echo "NAMESPACE: $NAMESPACE"
```


## Install helm chart
```
export DEPLOYMENT_NAME="nginx-example"

helm install $DEPLOYMENT_NAME \
--create-namespace \
--namespace $NAMESPACE \
.

```

## Test helm chart changes
For testing the helm templating result you can execute the helm template command.
When you want to use yml syntax highlighting on the console install yq and pipe the results from the template command to yq eval.

```
helm template testdeploymentname . | yq eval
```

## Ingress

Enable it with `ingress.enabled: true`. Two things the example exists to teach:

- The route is answered by an IngressClass, and on a kubementat cluster that class is Traefik. The platform
  installs it with `ingressClass.isDefaultClass: true`, so it is also the class that answers for an Ingress
  that names none. `ingress.className` is written anyway, because the example has to stay honest on a
  cluster that serves someone else's default too.
- Nothing asks for an HTTPS redirect. kubementat's Traefik redirects the `web` entrypoint to `websecure`
  cluster-wide (see `src/kubementat/assets/values/traefik/values.encrypted.yaml`),
  which is one setting owned by the platform instead of one annotation per Ingress.

## Deletion
```
helm delete $DEPLOYMENT_NAME
```