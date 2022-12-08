#!/usr/bin/env bash



ENVIRONMENT="$1"

if [[ "$ENVIRONMENT" == "" ]]; then
  echo "Usage: generate_linkerd_certs.sh <ENVIRONMENT_NAME"
  echo "e.g.: generate_linkerd_certs.sh dev"
  exit 1
fi

set -eu

### generate CA certs for linkerd

CERTIFICATE_VALIDITY_IN_DAYS="730" # 2 years default
LINKERD_CONFIG_DIRECTORY="../../../platform_config/${ENVIRONMENT}/kubementat_components/linkerd"
CA_PRIVATE_KEY_FILENAME="${LINKERD_CONFIG_DIRECTORY}/linkerd_ca_private_key.encrypted.pem"
CA_PUBLIC_KEY_FILENAME="${LINKERD_CONFIG_DIRECTORY}/linkerd_ca_public_key.encrypted.pem"
ISSUER_PRIVATE_KEY_FILENAME="${LINKERD_CONFIG_DIRECTORY}/linkerd_issuer_private_key.encrypted.pem"
ISSUER_PUBLIC_KEY_FILENAME="${LINKERD_CONFIG_DIRECTORY}/linkerd_issuer_public_key.encrypted.pem"
ISSUER_CSR_FILENAME="${LINKERD_CONFIG_DIRECTORY}/linkerd_issuer.encrypted.csr"
ISSUER_CRT_FILENAME="${LINKERD_CONFIG_DIRECTORY}/linkerd_issuer.encrypted.crt"
CA_FILENAME="${LINKERD_CONFIG_DIRECTORY}/linkerd_ca.encrypted.crt"

# Create CA private key
if [ ! -f "$CA_PRIVATE_KEY_FILENAME" ]; then
    openssl ecparam -name prime256v1 -genkey -noout -out "$CA_PRIVATE_KEY_FILENAME"
fi

# Create CA public key
openssl ec -in "$CA_PRIVATE_KEY_FILENAME" -pubout -out "$CA_PUBLIC_KEY_FILENAME"

# Create self signed CA certificate
openssl req -x509 -new -key "$CA_PRIVATE_KEY_FILENAME" -days "$CERTIFICATE_VALIDITY_IN_DAYS" -out "$CA_FILENAME" -subj "/CN=root.linkerd.cluster.local"

# Create issuer private key
if [ ! -f "$ISSUER_PRIVATE_KEY_FILENAME" ]; then
    openssl ecparam -name prime256v1 -genkey -noout -out "$ISSUER_PRIVATE_KEY_FILENAME"
fi

# Create issuer public key
openssl ec -in "$ISSUER_PRIVATE_KEY_FILENAME" -pubout -out "$ISSUER_PUBLIC_KEY_FILENAME"

# Create certificate signing request (BUG: the extension added here will be ignored by the signing)
openssl req -new -key "$ISSUER_PRIVATE_KEY_FILENAME" -out "$ISSUER_CSR_FILENAME" -subj "/CN=identity.linkerd.cluster.local" \
    -addext "basicConstraints=critical,CA:TRUE"

# Create issuer cert by signing request
openssl x509 \
    -extfile /etc/ssl/openssl.cnf \
    -extensions v3_ca \
    -req \
    -in "$ISSUER_CSR_FILENAME" \
    -days "$CERTIFICATE_VALIDITY_IN_DAYS" \
    -CA "$CA_FILENAME" \
    -CAkey "$CA_PRIVATE_KEY_FILENAME" \
    -CAcreateserial \
    -extensions v3_ca \
    -out "$ISSUER_CRT_FILENAME"

# remove unneeded csr file, as this can be regenerated
rm "$ISSUER_CSR_FILENAME"