#!/bin/sh
# Bounded local-host and public HTTPS checks; no guest network configuration changes.
H=${H:-http://10.0.2.2:18769}
tag=${1:-network}
f=/tmp/$tag.txt
{
    echo "NETWORK_BEGIN $tag"
    date -u +%Y-%m-%dT%H:%M:%SZ
    sw_vers
    echo HOST_HTTP
    curl -4 -sS --connect-timeout 5 -m 15 -o /tmp/$tag-host.txt \
        -w 'http_code=%{http_code} remote_ip=%{remote_ip}\n' "$H/get/probe.sh"
    echo "host_exit=$?"
    echo DNS_AND_HTTPS
    curl -4 -sS --connect-timeout 10 -m 30 -o /tmp/$tag-iana.html \
        -w 'http_code=%{http_code} remote_ip=%{remote_ip} ssl_verify_result=%{ssl_verify_result} dns_seconds=%{time_namelookup}\n' \
        https://www.iana.org/
    echo "https_exit=$?"
    echo NETWORK_END
} > "$f" 2>&1
cat "$f"
curl -sS --connect-timeout 5 -m 15 -T "$f" "$H/up/$tag.txt" && echo "uploaded $tag.txt"
