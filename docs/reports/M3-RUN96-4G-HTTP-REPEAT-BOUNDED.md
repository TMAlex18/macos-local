# M3 Run96 — 4 GiB HTTP repeat after bounded watcher repair

Run `m3-memory-4g-http-20261105-96` used the same 4 GiB, 8-vCPU Intel
profile and promoted binary as Runs 94–95. The guest reached normal macOS and a
new Safari window rendered the host-served 185-byte RGB fixture. Native capture
`r96-http.png` has the same exact primary-color counts as Runs94/95: red
78,517, green 78,480 and blue 78,480.

The bounded runner completed 1800 seconds with `collection_success=true`,
QEMU exit 0 and reaping after host QMP teardown. No guest-origin shutdown was
observed. The revised `gpuwatch.sh` was fetched, but no `r96fresh.tgz` upload
arrived before teardown; therefore this run supplies another HTTP page
repeatability point but no WebKit watcher timing or clean-filesystem evidence.
