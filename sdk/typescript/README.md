# @converge.ai/foundation-sdk

TypeScript SDK package for Agent Foundation Service.

## Status

This `0.0.x` package reserves the stable npm package and module names while the service API is being designed. It intentionally exposes no client API yet. Generated models and transports will be added only after the service contract is stable enough to support compatibility guarantees.

## Installation

```bash
npm install @converge.ai/foundation-sdk
```

```typescript
import "@converge.ai/foundation-sdk";
```

## Development

Run the TypeScript SDK checks from the repository root:

```bash
make sdk-typescript-check
```

## Publishing

The first public version must be published locally to establish the npm package:

```bash
cd sdk/typescript
npm ci
npm run check:all
npm publish --access public
```

The publishing account must have write access to the `converge.ai` npm organization and either complete 2FA or use a temporary granular access token that can bypass 2FA. Revoke the bootstrap token immediately after trusted publishing is configured.

Configure the existing package to trust the exact GitHub workflow and Environment. `npm trust` requires npm 11.15 or newer and interactive account authentication with 2FA; a bypass-2FA granular token cannot configure trust:

```bash
npx -y npm@11.19.0 trust github @converge.ai/foundation-sdk \
  --repo converge-ai-labs/agent-foundation \
  --file release-sdk-typescript.yml \
  --environment sdk-typescript-npm \
  --allow-publish \
  --yes

npx -y npm@11.19.0 trust list @converge.ai/foundation-sdk
```

Subsequent versions are published from `.github/workflows/release-sdk-typescript.yml` with npm Trusted Publishing. Push `release/sdk/typescript/<version>`, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`; the workflow injects that version into `package.json` and `package-lock.json` in its ephemeral checkout. RCs publish under the npm `rc` dist-tag and never advance `latest`.

## License

Licensed under the Apache License 2.0.
