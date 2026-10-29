# Bundled third-party libraries

Kepler73's web page loads these from here, not from a CDN, so it works on a computer
that has no internet access at all. They are unmodified copies; each folder keeps its
own `LICENSE`.

| Library | Version | Licence | Files | Source |
|---|---|---|---|---|
| [Leaflet](https://leafletjs.com) | 1.9.4 | BSD 2-Clause | `leaflet/leaflet.js`, `leaflet/leaflet.css`, `leaflet/images/*` | `unpkg.com/leaflet@1.9.4/dist/` |
| [Socket.IO client](https://socket.io) | 4.7.2 | MIT | `socket.io/socket.io.min.js` | `cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/` |

The three script/style files match the integrity hashes published for those releases:

```
leaflet.js          sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=
leaflet.css         sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=
socket.io.min.js    sha384-mZLF4UVrpi/QTWPA7BjNPEnkIfRFn4ZEO3Qt/HFklTJBj/gBOV8G3HcKn4NfQblz
```

To update one, replace the files, check the new hash against the library's release notes,
and bump the `?v=` of anything that references them.
