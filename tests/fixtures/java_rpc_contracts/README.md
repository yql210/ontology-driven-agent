# Java RPC cross-repository contract fixtures

These public, synthetic Java sources freeze the D1 inputs for the Java/Dubbo
cross-repository method-dependency work. `sample-order-contract` is API-only.
`sample-checkout-consumer` deliberately imports that API rather than copying it.
`provider-client-module` is the equivalent two-repository, provider/client-module
layout. `expected.json` is the immutable acceptance manifest for these fixtures.
