# busybox public
NODE_NAME="NODE_NAME"
IMAGE_URL="busybox:latest"
DOCKER_REGISTRY_SECRET_NAME="docker-registry-secret"
kubectl run busybox-shell-public-node01 --rm -i --tty --image busybox:latest --overrides="{ \"apiVersion\": \"v1\", \"spec\": { \"nodeName\": \"$NODE_NAME\" } }" --command /bin/sh

# busybox private registry
kubectl run busybox-shell-node01 --rm -i --tty --image="$IMAGE_URL" --overrides="{ \"apiVersion\": \"v1\", \"spec\": { \"nodeName\": \"$NODE_NAME\", \"imagePullSecrets\": [{\"name\": \"$DOCKER_REGISTRY_SECRET_NAME\"}]} }" --command /bin/sh