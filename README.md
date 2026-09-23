# target-shopify-v2

`target-shopify-v2` is a Singer target for TargetShopifyV2.

Built with the [Hotglue Singer SDK](https://github.com/hotgluexyz/HotglueSingerSDK) (`hotglue-singer-sdk`).

## Installation

```bash
pip install .
# or editable for local development:
pip install -e .
```

## Configuration

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `shop` | string | Yes | Shopify store subdomain (e.g. `your-store` for `your-store.myshopify.com`) |
| `access_token` | string | No* | OAuth access token for the Admin API (`X-Shopify-Access-Token`). Preferred over `api_key`. |
| `api_key` | string | No* | Admin API key (private app token). Used when `access_token` is not set. *At least one of `access_token` or `api_key` is required.* |

**Minimal `config.json`:**

```json
{
  "shop": "your-store-name",
  "access_token": "shpat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

A full list of supported settings and capabilities for this
target is available by running:

```bash
target-shopify-v2 --about
```

### Configure using environment variables

This Singer target will automatically import any environment variables within the working directory's
`.env` if the `--config=ENV` is provided, such that config values will be considered if a matching
environment variable is set either in the terminal context or in the `.env` file.

## Usage

You can run `target-shopify-v2` by itself or as a target in a [hotglue](https://hotglue.com) flow.

### Executing the Target Directly

```bash
target-shopify-v2 --version
target-shopify-v2 --help
target-shopify-v2 --config /path/to/config.json --input /path/to/data.singer
# or pipe Singer messages:
tap-something | target-shopify-v2 --config /path/to/config.json
```

## Developer Resources

### Initialize your Development Environment

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e .
# or: uv pip install -e .
```

### Lint

```bash
tox
# runs ruff check . (pinned <0.16 in tox.ini)
```

### Create and Run Tests

Create tests within the `target_shopify_v2/tests` subfolder and then run:

```bash
pytest
```

### SDK

See [HotglueSingerSDK](https://github.com/hotgluexyz/HotglueSingerSDK) for target/sink base classes (`TargetHotglue`, `HotglueSink`).
