#!/usr/bin/env bash

TEKTON_NAMESPACE="tekton-pipelines"
GRAFANA_NAMESPACE="grafana"
POLARIS_NAMESPACE="polaris"

function check_cluster_and_access(){
  echo "Checking cluster access"
  echo "You are going to edit users on the following cluster:"
  kubectl cluster-info

  while true; do
    read -p "Do you really wish to edit users on this cluster?" yn
    case $yn in
        [Yy]* ) break;;
        [Nn]* ) echo "Cancelled script."; exit;;
        * ) echo "Please answer yes or no.";;
    esac
  done

  kubectl auth can-i create namespace

  kubectl auth can-i create serviceaccount
  kubectl auth can-i update serviceaccount
  kubectl auth can-i patch serviceaccount

  kubectl auth can-i create role
  kubectl auth can-i update role
  kubectl auth can-i patch role

  kubectl auth can-i create rolebinding
  kubectl auth can-i update rolebinding
  kubectl auth can-i patch rolebinding

  echo "Finished checking cluster access"
  echo "################"
  echo ""
}

#### SA ####

function create_service_account_in_namespace(){
    SERVICE_ACCOUNT_NAME="$1"
    NAMESPACE="$2"
    
    echo "Configuring service account $SERVICE_ACCOUNT_NAME in namespace $NAMESPACE ..."
    kubectl -n "$NAMESPACE" apply  -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: $SERVICE_ACCOUNT_NAME
EOF
    echo "Finished configuring service account $SERVICE_ACCOUNT_NAME in namespace $NAMESPACE."
    echo "#########################"
    echo ""
}

function create_service_account_secret_in_namespace(){
    SERVICE_ACCOUNT_SECRET_NAME="$1"
    SERVICE_ACCOUNT_NAME="$2"
    NAMESPACE="$3"
    echo "Configuring service account secret token $SERVICE_ACCOUNT_SECRET_NAME in namespace $NAMESPACE ..."
    kubectl -n "$NAMESPACE" apply  -f - <<EOF
apiVersion: v1
kind: Secret
type: kubernetes.io/service-account-token
metadata:
 name: $SERVICE_ACCOUNT_SECRET_NAME
 annotations:
  "kubernetes.io/service-account.name": "$SERVICE_ACCOUNT_NAME"
EOF
    echo "Finished configuring service account secret token $SERVICE_ACCOUNT_SECRET_NAME in namespace $NAMESPACE ."
    echo "#########################"
    echo ""
}


### ROLES ###

function create_namespace_admin_role(){
    ROLE_NAME="namespace-admin"
    NAMESPACE="$1"
    echo "Configuring role: $ROLE_NAME in namespace $NAMESPACE ..."
    kubectl -n "$NAMESPACE" apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: $ROLE_NAME
rules:
- apiGroups: ["", "rbac.authorization.k8s.io", "networking.k8s.io", "batch", "extensions", "apps", "autoscaling", "tekton.dev"]
  resources: ["*"]
  verbs: ["*"]
EOF
    echo "Finished configuring role: $ROLE_NAME in namespace $NAMESPACE ."
    echo ""
}

function create_namespace_read_only_role(){
    ROLE_NAME="namespace-readonly"
    NAMESPACE="$1"
    echo "Configuring role: $ROLE_NAME in namespace $NAMESPACE ..."
    kubectl -n "$NAMESPACE" apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: $ROLE_NAME
rules:
- apiGroups: ["", "rbac.authorization.k8s.io", "networking.k8s.io", "batch", "extensions", "apps", "autoscaling", "tekton.dev"]
  resources: ["*"]
  verbs: ["get", "list"]
- apiGroups: [""]
  resources: ["pods/portforward"]
  verbs: ["get", "list", "create"]
EOF
    echo "Finished configuring role: $ROLE_NAME in namespace $NAMESPACE ."
    echo ""
}

function create_tekton_tunneling_role(){
    ROLE_NAME="tekton-tunneling"
    echo "Configuring role: $ROLE_NAME in namespace $TEKTON_NAMESPACE ..."
    kubectl -n "$TEKTON_NAMESPACE" apply  -f - <<EOF
kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: $ROLE_NAME
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list"]
- apiGroups: [""]
  resources: ["pods/portforward"]
  verbs: ["get", "list", "create"]
EOF
    echo "Finished configuring role: $ROLE_NAME in namespace $TEKTON_NAMESPACE ."
    echo ""
}

function create_grafana_tunneling_role(){
    ROLE_NAME="grafana-tunneling"
    echo "Configuring role: $ROLE_NAME in namespace $GRAFANA_NAMESPACE ..."
    kubectl -n "$GRAFANA_NAMESPACE" apply  -f - <<EOF
kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: $ROLE_NAME
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list"]
- apiGroups: [""]
  resources: ["pods/portforward"]
  verbs: ["get", "list", "create"]
EOF
    echo "Finished configuring role: $ROLE_NAME in namespace $GRAFANA_NAMESPACE ."
    echo ""
}

function create_polaris_tunneling_role(){
    ROLE_NAME="polaris-tunneling"
    echo "Configuring role: $ROLE_NAME in namespace $POLARIS_NAMESPACE ..."
    kubectl -n "$POLARIS_NAMESPACE" apply  -f - <<EOF
kind: Role
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: $ROLE_NAME
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list"]
- apiGroups: [""]
  resources: ["pods/portforward"]
  verbs: ["get", "list", "create"]
EOF
    echo "Finished configuring role: $ROLE_NAME in namespace $POLARIS_NAMESPACE ."
    echo ""
}

function bind_namespace_role_to_service_account(){
    ROLE_NAME="$1"
    ROLE_NAMESPACE="$2"
    SERVICE_ACCOUNT_NAME="$3"
    SERVICE_ACCOUNT_NAMESPACE="$4"

    echo "Binding Role $ROLE_NAME in namespace $ROLE_NAMESPACE to service account $SERVICE_ACCOUNT_NAME in namespace $SERVICE_ACCOUNT_NAMESPACE ..."

    kubectl apply -n "$ROLE_NAMESPACE" -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${ROLE_NAME}-${SERVICE_ACCOUNT_NAME}-binding
subjects:
- kind: ServiceAccount
  name: ${SERVICE_ACCOUNT_NAME}
  namespace: ${SERVICE_ACCOUNT_NAMESPACE}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: ${ROLE_NAME}
EOF
    echo "Finished binding Role $ROLE_NAME in namespace $ROLE_NAMESPACE to service account $SERVICE_ACCOUNT_NAME in namespace $SERVICE_ACCOUNT_NAMESPACE ."
    echo ""
}

function delete_role(){
    ROLE_NAME="$1"
    ROLE_NAMESPACE="$2"

    echo "Deleting role $ROLE_NAME in namespace $ROLE_NAMESPACE ..."
    kubectl -n "$ROLE_NAMESPACE" delete role "$ROLE_NAME"

    echo "Finished deleting role $ROLE_NAME in namespace $ROLE_NAMESPACE ."
    echo ""
}

function delete_role_binding(){
    ROLE_BINDING_NAME="$1"
    ROLE_BINDING_NAMESPACE="$2"

    echo "Deleting role binding $ROLE_BINDING_NAME in namespace $ROLE_BINDING_NAMESPACE ..."
    kubectl -n "$ROLE_BINDING_NAMESPACE" delete rolebinding "$ROLE_BINDING_NAME"

    echo "Finished deleting role binding $ROLE_BINDING_NAME in namespace $ROLE_BINDING_NAMESPACE ."
    echo ""
}

function delete_service_account(){
    SERVICE_ACCOUNT_NAME="$1"
    SERVICE_ACCOUNT_NAMESPACE="$2"

    echo "Deleting service account $SERVICE_ACCOUNT_NAME in namespace $SERVICE_ACCOUNT_NAMESPACE ..."
    kubectl -n "$SERVICE_ACCOUNT_NAMESPACE" delete serviceaccount "$SERVICE_ACCOUNT_NAME"

    echo "Finished deleting service account $SERVICE_ACCOUNT_NAME in namespace $SERVICE_ACCOUNT_NAMESPACE ."
    echo ""
}

function print_kubeconfig_for_service_account(){
    ENVIRONMENT="$1"
    TEAM="$2"
    SERVICE_ACCOUNT_NAME="$3"
    SERVICE_ACCOUNT_NAMESPACE="$4"
    SERVICE_ACCOUNT_SECRET_NAME="$5"

    ca_crt_data="$(kubectl -n "$SERVICE_ACCOUNT_NAMESPACE" get secret "$SERVICE_ACCOUNT_SECRET_NAME" -o=jsonpath='{.data.ca\.crt}')"
    token="$(kubectl -n "$SERVICE_ACCOUNT_NAMESPACE" get secret "$SERVICE_ACCOUNT_SECRET_NAME" -o=jsonpath='{.data.token}' | base64 -d)"

    # echo "TOKEN:"
    # echo  "$token"
    # echo "CA.crt:"
    # echo  "$ca_crt_data"

    # retrieve local kubeconfig settings
    certificate_authority_data="$(kubectl config view --flatten --minify | yq eval -j | jq -r '.clusters[0].cluster."certificate-authority-data"')"
    server="$(kubectl config view --flatten --minify | yq eval -j | jq -r '.clusters[0].cluster.server')"
    # echo "certificate_authority_data:"
    # echo  "$certificate_authority_data"
    # echo "server:"
    # echo  "$server"

    echo ""
    echo "###########################"
    echo "Please put the settings below in your ~/.kube/config file:"
    echo "###########################"
    echo ""

    CLUSTER_NAME="${ENVIRONMENT}-cluster"
    CONTEXT_NAME="${ENVIRONMENT}-cluster-${TEAM}-${SERVICE_ACCOUNT_NAME}"

    cat <<EOF
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: ${certificate_authority_data}
    server: ${server}
  name: ${ENVIRONMENT}-cluster
contexts:
- context:
    cluster: ${CLUSTER_NAME}
    namespace: ${APP_DEPLOYMENT_NAMESPACE}
    user: ${SERVICE_ACCOUNT_NAME}
  name: ${CONTEXT_NAME}
current-context: ${CONTEXT_NAME}
kind: Config
preferences: {}
users:
- name: ${SERVICE_ACCOUNT_NAME}
  user:
    token: ${token}
EOF

    echo ""
    echo "##########"
}

function print_account_info_for_namespace(){
    NAMESPACE="$1"

    echo "###"
    echo "$NAMESPACE:"
    echo ""
    echo "  Service Accounts:"
    kubectl get serviceaccounts -n $NAMESPACE
    echo "  Roles:"
    kubectl get roles -n $NAMESPACE
    echo "  Rolebindings:"
    kubectl get rolebinding -n $NAMESPACE
    echo ""
    echo "###"
    echo ""
}