#!/usr/bin/env bash

#################################
#
# Outputs an ingress definition yml file with:
# - tls certificate configuration via cert-manager and letsencrypt
# - nginx ingress controller routing
#################################

set -e

DOMAIN="$1"
SERVICE_NAME="$2"
SERVICE_PORT="$3"

if [[ "$SERVICE_PORT" == "" || "$SERVICE_NAME" == "" || "$DOMAIN" == "" ]]; then
  echo "Usage: create_ingress_yml.sh <DOMAIN> <SERVICE_NAME> <SERVICE_PORT>"
  echo "e.g.: create_ingress_yml.sh 'nginx-example.dev1.yourdomain.com' nginx-example 80"
  exit 1
fi

# CONSTANTS
CLUSTER_ISSUER_NAME="letsencrypt"
INGRESS_CLASS="nginx"
TLS_ACME_ENABLED="true"
SSL_REDIRECT_ENBALED="true"

cat << EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  annotations:
    # add an annotation indicating the issuer to use.
    cert-manager.io/cluster-issuer: $CLUSTER_ISSUER_NAME
    kubernetes.io/tls-acme: "$TLS_ACME_ENABLED"
    kubernetes.io/ingress.class: $INGRESS_CLASS
    nginx.ingress.kubernetes.io/ssl-redirect: "$SSL_REDIRECT_ENBALED"
  name: ${SERVICE_NAME}-ingress
spec:
  rules:
  - host: $DOMAIN
    http:
      paths:
      - pathType: Prefix
        path: /
        backend:
          service:
            name: $SERVICE_NAME
            port:
              number: $SERVICE_PORT
  tls: # < placing a host in the TLS config will determine what ends up in the cert's subjectAltNames
  - hosts:
    - $DOMAIN
    secretName: ${SERVICE_NAME}-cert
EOF