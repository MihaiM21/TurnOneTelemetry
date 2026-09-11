# CHANGELOG

<!-- version list -->

## v1.7.0 (2026-09-11)

### Bug Fixes

- Qualifying session results (when the GP has a sprint shootout the quali for the race returns the
  results from sprint shootout, now fixed)
  ([`6718a98`](https://github.com/MihaiM21/TurnOneTelemetry/commit/6718a98c088b4606b20ab130aef1dfeeb5aec65a))

- Security issues for keys
  ([`b149760`](https://github.com/MihaiM21/TurnOneTelemetry/commit/b14976030c9d0345aa2989b00e69ea40a5337c8f))

- **analysis-v2**: Correct corner-duel numbering, add clean-lap selection, new lap-all-data endpoint
  ([`3befa17`](https://github.com/MihaiM21/TurnOneTelemetry/commit/3befa17c5c1af1f7b728a3bc1b6452b55b15f46d))

- **repositories**: Add season-scope storage, admin flag setter, and batched key-usage stats
  ([`c4ffe71`](https://github.com/MihaiM21/TurnOneTelemetry/commit/c4ffe7135e4b2e7b517fdf989dc36d8d7db7bbc6))

### Features

- Admin storage cleanup across all cache layers
  ([`4f9674a`](https://github.com/MihaiM21/TurnOneTelemetry/commit/4f9674a1b7852cf691bae172d5b986caf2c385e3))

- Better circuit integration and processing and added madring also
  ([`a3b9929`](https://github.com/MihaiM21/TurnOneTelemetry/commit/a3b99292d4fb630bd0754929c2664630d66d36e6))

- CSV export for v1 and v2 JSON endpoints
  ([`fa124cc`](https://github.com/MihaiM21/TurnOneTelemetry/commit/fa124cc031927a08a41f5f54c7f54b90e244c96a))

- Driver image and team logo media endpoints
  ([`10299eb`](https://github.com/MihaiM21/TurnOneTelemetry/commit/10299ebfc52ac6ad8f9c5cc914d17e1607a7a4b7))

- Enhance telemetry data handling with SessionDataStore integration
  ([`0fa2576`](https://github.com/MihaiM21/TurnOneTelemetry/commit/0fa257603668bfe92dff8dfea937c7cd078bc735))

- Feature discovery and batch endpoints
  ([`3205a42`](https://github.com/MihaiM21/TurnOneTelemetry/commit/3205a4267784ca3412a84fccfe1936c071024e5b))

- Improved swagger (phase 2) and improved logging + removing old printlines
  ([`8a69271`](https://github.com/MihaiM21/TurnOneTelemetry/commit/8a692716706c20e5cade4581af4fe4d19cc0e103))

- Live drivers and constructors standings
  ([`6755c76`](https://github.com/MihaiM21/TurnOneTelemetry/commit/6755c762785113b59e3e824ee2b68415e7747f4a))

- Refactor circuit data handling and introduce new schemas
  ([`4892a3a`](https://github.com/MihaiM21/TurnOneTelemetry/commit/4892a3ab5b31fc3263d4667bbfc9e0b4448aa33f))

- Telemetry laps-data and track-map endpoints
  ([`63cbc5c`](https://github.com/MihaiM21/TurnOneTelemetry/commit/63cbc5c025e75bc75a36c57a5604a3f6c20f1e33))

- **admin**: Add V2 plot backfill engine and cache/data admin API
  ([`74259f3`](https://github.com/MihaiM21/TurnOneTelemetry/commit/74259f36da5bd5870566a36160924409db848e66))

- **admin-ui**: Rebuild admin console with shared layout and new pages
  ([`1f36d05`](https://github.com/MihaiM21/TurnOneTelemetry/commit/1f36d05dc69713f62c42002b5ed6a9e55ff7f9ab))

### Refactoring

- Share session-type validation across v2 features
  ([`5a3e8ea`](https://github.com/MihaiM21/TurnOneTelemetry/commit/5a3e8ea142cd8fea0ff38ff92b561863619ba6eb))


## v1.6.0 (2026-07-18)

### Bug Fixes

- Pipeline errors
  ([`521cd59`](https://github.com/MihaiM21/TurnOneTelemetry/commit/521cd59395a899a51a91d8148822476677762447))

### Features

- New plots available and better caching
  ([`ca2b010`](https://github.com/MihaiM21/TurnOneTelemetry/commit/ca2b01025a2aa8c4de6c8dc83ecea1b80b9b48c8))


## v1.5.0 (2026-06-20)

### Features

- Driver and team standings + teams and drivers pace analisys
  ([`f249a94`](https://github.com/MihaiM21/TurnOneTelemetry/commit/f249a9401939ad68d5707271843fcf76aa2cc504))


## v1.4.2 (2026-06-13)

### Bug Fixes

- Data aqcuisition
  ([`fe690c0`](https://github.com/MihaiM21/TurnOneTelemetry/commit/fe690c06c6fb341dee4678f3e275d75694a3c76a))


## v1.4.1 (2026-06-06)

### Bug Fixes

- Latest session more accurate finding
  ([`9de8dd2`](https://github.com/MihaiM21/TurnOneTelemetry/commit/9de8dd27531a9543ee7de717f18a679651f36fab))


## v1.4.0 (2026-06-03)

### Bug Fixes

- 2
  ([`a6d2dd7`](https://github.com/MihaiM21/TurnOneTelemetry/commit/a6d2dd7be7ebc819f68a250f3172bdeeb4d6c34e))

- Admin can view usage of the keys/users
  ([`6318ff9`](https://github.com/MihaiM21/TurnOneTelemetry/commit/6318ff941f0a53f08ae5c4e7f5d7c1d6a059991f))

- Admin page
  ([`e8209e2`](https://github.com/MihaiM21/TurnOneTelemetry/commit/e8209e262fb47b41737806c94ad4410fb8e036e1))

- Admin security
  ([`8b086cd`](https://github.com/MihaiM21/TurnOneTelemetry/commit/8b086cdd54287f968b3a9f2769894651e1ac5c5b))

- Implemented redis cache
  ([`4a32999`](https://github.com/MihaiM21/TurnOneTelemetry/commit/4a32999806fafed67ab9a2a855b7b92fde4cf109))

- Makefile and requirements.txt
  ([`536b568`](https://github.com/MihaiM21/TurnOneTelemetry/commit/536b568038bbc822bdccbd1c45e4f0a022641521))

- Pipeline and test coverage threshold
  ([`1cdf3b9`](https://github.com/MihaiM21/TurnOneTelemetry/commit/1cdf3b9653c2d2a28cc361ab6d27e3b3df896b63))

### Features

- Admin keys/users usage view
  ([`231dbbb`](https://github.com/MihaiM21/TurnOneTelemetry/commit/231dbbb1c0664c4182f49ce162536156d6ec53eb))

- Implemented api key creation and user db
  ([`d1a40fb`](https://github.com/MihaiM21/TurnOneTelemetry/commit/d1a40fbe30e5baf08d2b8fee8ad954b5aede4b37))


## v1.3.7 (2026-05-05)

### Bug Fixes

- Pipeline
  ([`77a2aa7`](https://github.com/MihaiM21/TurnOneTelemetry/commit/77a2aa7f91536d03e5b82167c276413fe3c4c9cf))


## v1.3.6 (2026-05-01)

### Bug Fixes

- Added fallback if our f1staticclient is not working
  ([`b5459e3`](https://github.com/MihaiM21/TurnOneTelemetry/commit/b5459e3207a37456c973d488963cc165072a28c0))


## v1.3.5 (2026-04-18)

### Bug Fixes

- Added cancelled for the bahrain and saudi arabian gps
  ([`944184e`](https://github.com/MihaiM21/TurnOneTelemetry/commit/944184ef8f4ea337af9726da168ce335c8f0584b))


## v1.3.4 (2026-04-07)

### Performance Improvements

- Added static data endpoints for driver and team data
  ([`24e2aa6`](https://github.com/MihaiM21/TurnOneTelemetry/commit/24e2aa629f3d39e62a9c476b7db8e7071c8cd43c))


## v1.3.3 (2026-04-03)

### Performance Improvements

- Docker files updated
  ([`862123a`](https://github.com/MihaiM21/TurnOneTelemetry/commit/862123ab35045e4c5ebc64a36a2a5f89447115dd))


## v1.3.2 (2026-04-02)

### Bug Fixes

- Dockerfile
  ([`ffa60ef`](https://github.com/MihaiM21/TurnOneTelemetry/commit/ffa60efb17e78ec8a0fcd6f6af6d81a870b56c7f))


## v1.3.1 (2026-04-02)

### Performance Improvements

- Added authorization login for the swagger
  ([`4bcb268`](https://github.com/MihaiM21/TurnOneTelemetry/commit/4bcb268323f2d7d49c67fac1365afb3297e78079))


## v1.3.0 (2026-03-13)

### Features

- Implemented new plot analysis type and refactored static client and dashboard endpoint
  ([`5a1187d`](https://github.com/MihaiM21/TurnOneTelemetry/commit/5a1187ddca7754db0b8fc8b5fcdf38f18cbaa5f1))


## v1.2.1 (2026-03-07)

### Bug Fixes

- Bug with saving to mongodb
  ([`f5a4702`](https://github.com/MihaiM21/TurnOneTelemetry/commit/f5a47027d5e685004a8da31d84e98530cab8189b))


## v1.2.0 (2026-03-01)

### Bug Fixes

- Top speed v2
  ([`643fcd6`](https://github.com/MihaiM21/TurnOneTelemetry/commit/643fcd65301263abc358079903c898dbd0e37a32))

### Features

- Added new endpoints for available grand prix and sessions from them
  ([`226e67c`](https://github.com/MihaiM21/TurnOneTelemetry/commit/226e67c65d0f491c4d8dc60fca067198da779959))

- Added throttle comparison v2 and improved our static client data mapping
  ([`8daf146`](https://github.com/MihaiM21/TurnOneTelemetry/commit/8daf146b05554f31e7a6cd942a6a8bc339b2e360))

- V2 can now accept gp key, gp official name or round number
  ([`60faad7`](https://github.com/MihaiM21/TurnOneTelemetry/commit/60faad72ce66ea3e0e83c0276a808c54cd7aa937))


## v1.1.0 (2026-02-07)

### Bug Fixes

- Lando norris number for the 2026 season (4 -> 1)
  ([`1be0535`](https://github.com/MihaiM21/TurnOneTelemetry/commit/1be0535d8723384964c8e0dd3a026c6ed550f527))

### Features

- Added monitoring
  ([`55ede44`](https://github.com/MihaiM21/TurnOneTelemetry/commit/55ede44ba256b4a6d56200c6f13d3a2891738e07))


## v1.0.2 (2026-01-01)

### Bug Fixes

- 2 drivers comparison data process
  ([`cbfbcc2`](https://github.com/MihaiM21/TurnOneTelemetry/commit/cbfbcc2520fb27080f9b526675bce5001e30e284))


## v1.0.1 (2025-12-31)

### Bug Fixes

- Docker compose
  ([`40dcd6e`](https://github.com/MihaiM21/TurnOneTelemetry/commit/40dcd6ec41cddd6af0add07eaaa1328fc49dc5eb))


## v1.0.0 (2025-12-31)

### Bug Fixes

- Docker
  ([`7f3c50a`](https://github.com/MihaiM21/TurnOneTelemetry/commit/7f3c50af908465638b58782feece7f2257b00764))

- Docker compose
  ([`464c7ea`](https://github.com/MihaiM21/TurnOneTelemetry/commit/464c7eab0f0d2848f1532cf790eeb0587612999a))

- Laptimes distribution
  ([`9341f64`](https://github.com/MihaiM21/TurnOneTelemetry/commit/9341f648ef24601a15691d75835fbe6003a35ed8))

- Latest session
  ([`2c7be01`](https://github.com/MihaiM21/TurnOneTelemetry/commit/2c7be01251dd66b3139fe660623a552c8032688c))

### Features

- Pipeline and versioning update
  ([`027504e`](https://github.com/MihaiM21/TurnOneTelemetry/commit/027504eed9dab6e801b0f81da51ca6a164534817))


## v0.1.0 (2025-12-12)

- Initial Release
