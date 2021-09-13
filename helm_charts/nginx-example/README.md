# Smart Telematics Web UI Helm Chart

This chart is used for deploying nginx as an example for implementing custom helm charts into easy_tekton automation.

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

## Deletion
```
helm delete $DEPLOYMENT_NAME
```