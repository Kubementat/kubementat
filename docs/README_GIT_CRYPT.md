# Git crypt documentation

Everything between "setup" and "Usage" below is what `kmt config init` does for itself, and is
written out only so a reader knows which `git-crypt` commands the verb is a wrapper around. Nobody is
supposed to run it by hand: `config init` orders these steps, declares `.gitattributes` before the
first encrypted file, and commits once when all of them have run. Running them yourself against a
directory `config init` already prepared is how you end up with a `.gitattributes` that is not the one
the verb checks.

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

# this is what a fresh directory needs, in this order; config init does it
#   - git init, before any git-crypt command
#   - the deployer's own key generated or imported first (step 4), because add-gpg-user cannot read a file
#   - git-crypt init
#   - .gitattributes declared before the first encrypted file is written
#   - the deployer registered, by fingerprint and not by email address (see the note under step 7 below)
#   - the symmetric key exported at mode 0600
# adding the deployer's key is what registers it; a key only imported into the local keyring is
# refused unless it is marked trusted, so a machine with no public-key material still gets one.
git-crypt init

# setup configuration
echo "*.encrypted.* filter=git-crypt diff=git-crypt" > .gitattributes

# add previously generated deployer key to git-crypt keystore.
# Resolve the fingerprint from THIS directory's key file and not from the keyring, because a keyring
# listing for one email returns a list, and with two configuration trees under one email that list
# contains the other tree's key. Registering one of those succeeds silently and leaves a directory
# that installs and then cannot decrypt its own secrets.
DEPLOYER_FPR="$(gpg --with-colons --show-keys gpg_public_key.gpg | awk -F: '/^fpr/{print $10; exit}')"
git-crypt add-gpg-user --no-commit --trusted "$DEPLOYER_FPR"

# optional: add another key, same rule — a fingerprint beats an email whenever you have one
git-crypt add-gpg-user --no-commit --trusted "<fingerprint>"

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