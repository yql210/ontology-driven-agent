# Neutral three-repository Service Graph fixture

`provider-orders` exposes `GET /orders/{id}` and `POST /orders` as `orders:Order`.
`consumer-checkout` calls the provider and includes a dynamic URL unresolved by design.
`isolated-catalog` is unrelated and must not produce a provider endpoint.
