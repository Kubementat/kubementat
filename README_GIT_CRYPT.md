# Git crypt documentation

# setup
```
# list all local gpg keys
gpg -k

# generate a gpg key for the deployer user
DEPLOYER_EMAIL="deployer@deployment.com"
gpg --quick-generate-key --yes "$DEPLOYER_EMAIL"

gpg --list-secret-keys "$DEPLOYER_EMAIL"

# export private key
gpg --export-secret-keys "$DEPLOYER_EMAIL" > gpg_private.key

# export public key
gpg --output gpg_public_key.gpg --export "$DEPLOYER_EMAIL"

# for importing:

# private key
# gpg --import private.key

# public key
gpg --import gpg_public_key.gpg

# for transforming to base64
## ATTENTION: base64 behaves differently on some Operating system

### OS X Variant
cat private.key | base64

### Arch linux variant
cat private.key | base64 -w 0

# initialize (only executed when initializing a new repo with git crypt)
git-crypt init

# setup configuration
echo "*.encrypted.* filter=git-crypt diff=git-crypt" > .gitattributes

# add previously generated deployer user to git-crypt keystore
git-crypt add-gpg-user "$DEPLOYER_EMAIL"

# optional: add another key
git-crypt add-gpg-user "your@email.com"

# show git-crypt encryption status
git-crypt status

# optional: export a symmetric key for git crypt
git-crypt export-key git_crypt_symmetric.key
```

# Usage
```
# if you have a private key registered
git-crypt unlock

# for the symmetric key
git-crypt unlock git_crypt_symmetric.key
```