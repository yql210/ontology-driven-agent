# Neutral three-repository gRPC fixture

`provider-orders` implements `OrderServiceGrpc/GetOrder`.
`consumer-checkout` calls it through generated blocking and async stubs.
`isolated-catalog` implements a same-name RPC under `CatalogServiceGrpc`.
