# Vendored front-end libraries

Served from our own origin so the app does not depend on third-party CDNs
(availability, privacy, and a future Content-Security-Policy).

| File | Package | License |
|---|---|---|
| `chart-4.4.1.umd.js` | chart.js 4.4.1 | MIT |
| `sortable-1.15.2.min.js` | sortablejs 1.15.2 | MIT |
| `quill-2.0.2.js`, `quill-2.0.2.snow.css` | quill 2.0.2 | BSD-3-Clause |
| `inter.css`, `fonts/inter-*.woff2` | @fontsource/inter 5.0.16 | SIL OFL 1.1 |
| `plex.css`, `fonts/ibm-plex-sans-*.woff2` | @fontsource/ibm-plex-sans 5.2.5 | SIL OFL 1.1 |
| `fonts/ibm-plex-mono-*.woff2` | @fontsource/ibm-plex-mono 5.1.1 | SIL OFL 1.1 |

To upgrade: `npm pack <package>@<version>`, copy the dist file here under a
versioned name and update the reference in the template.
