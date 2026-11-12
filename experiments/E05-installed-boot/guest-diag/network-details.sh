#!/bin/sh
# Bounded read-only DHCP/address/route queries; no network settings are changed.
H=${H:-http://10.0.2.2:18769}
tag=${1:?tag required}
case "$tag" in *[!A-Za-z0-9_-]*|'') exit 2;; esac
d=/tmp/$tag
mkdir "$d" || exit 1
bounded_read() {
    name=$1; shift
    "$@" > "$d/$name.txt" 2>&1 &
    child=$!
    count=0
    while kill -0 "$child" 2>/dev/null && [ "$count" -lt 10 ]; do
        sleep 1; count=$((count + 1))
    done
    echo "QUERY=$name"
    if kill -0 "$child" 2>/dev/null; then
        kill -TERM "$child" 2>/dev/null
        sleep 1
        kill -KILL "$child" 2>/dev/null
        echo "timeout=true cleanup_requested_pid=$child"
    else
        wait "$child"
        echo "exit=$?"
    fi
    cat "$d/$name.txt"
}
{
    date -u +%Y-%m-%dT%H:%M:%SZ
    bounded_read route /sbin/route -n get default
    iface=$(awk '$1 == "interface:" { print $2; exit }' "$d/route.txt")
    echo "route_interface=$iface"
    case "$iface" in
        ''|*[!A-Za-z0-9_.-]*) echo 'interface_query=unavailable';;
        *) bounded_read address /usr/sbin/ipconfig getifaddr "$iface"
           bounded_read dhcp /usr/sbin/ipconfig getpacket "$iface";;
    esac
} > "$d/results.txt" 2>&1
cat "$d/results.txt"
curl -fsS --connect-timeout 5 -m 20 -T "$d/results.txt" "$H/up/$tag.txt" && echo "uploaded $tag.txt"
