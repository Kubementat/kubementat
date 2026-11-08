# The lane's build fixture: as small as possible so the pipeline's plumbing is the
# only variable. `scratch` pulls nothing from any registry, so no build depends on
# Docker Hub availability or its rate limits.
FROM scratch
COPY hello.txt /hello.txt
