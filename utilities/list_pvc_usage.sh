#!/usr/bin/env bash

KUBEAPI=127.0.0.1:8001/api/v1/nodes

function kubectl-proxy-start() {
    kubectl proxy &
}

function kubectl-proxy-kill() {
    pkill -9 -f "kubectl proxy"
}

function getNodes() {
  curl -s $KUBEAPI | jq -r '.items[].metadata.name'
}

function getPVCs() {
  jq -s '[flatten | .[].pods[].volume[]? | select(has("pvcRef")) | '\
'{name: .pvcRef.name, capacityBytes, usedBytes, availableBytes, '\
'percentageUsed: (.usedBytes / .capacityBytes * 100)}] | sort_by(.name)'
}

function column() {
  awk '{ for (i = 1; i <= NF; i++) { d[NR, i] = $i; w[i] = length($i) > w[i] ? length($i) : w[i] } } '\
'END { for (i = 1; i <= NR; i++) { printf("%-*s", w[1], d[i, 1]); for (j = 2; j <= NF; j++ ) { printf("%*s", w[j] + 1, d[i, j]) } print "" } }'
}

function defaultFormat() {
  awk 'BEGIN { print "PVC 1K-blocks Used Available Use%" } '\
'{$2 = $2/1024; $3 = $3/1024; $4 = $4/1024; $5 = sprintf("%.0f%%",$5); print $0}'
}

function humanFormat() {
  awk 'BEGIN { print "PVC Size Used Avail Use%" } '\
'{$5 = sprintf("%.0f%%",$5); printf("%s ", $1); system(sprintf("numfmt --to=iec %s %s %s | sed '\''N;N;s/\\n/ /g'\'' | tr -d \\\\n", $2, $3, $4)); print " " $5 }'
}

function format() {
  jq '.[] | "\(.name) \(.capacityBytes) \(.usedBytes) \(.availableBytes) \(.percentageUsed)"' |
    sed 's/^"\|"$//g' |
    $format | column
}

if [ "$1" == "-h" ]; then
  format=humanFormat
else
  format=defaultFormat
fi

kubectl-proxy-start
sleep 2

for node in $(getNodes); do
  curl -s $KUBEAPI/$node/proxy/stats/summary
done | getPVCs | format

kubectl-proxy-kill