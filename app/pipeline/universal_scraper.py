import asyncio
import json
import re
from urllib.parse import urljoin, urlparse
try:
    from selectolax.lexbor import LexborHTMLParser as HTMLParser
except ImportError:
    from selectolax.parser import HTMLParser
import pandas as pd
from .http_client import EcommerceHttpClient


def normalize_price(raw_value):
    """Clean arbitrary price string to float, prioritizing currency symbols and stripping HTML comments."""
    if raw_value is None:
        return None
    if isinstance(raw_value, (int, float)):
        return float(raw_value) if raw_value > 0 else None

    text = re.sub(r"<!--.*?-->", "", str(raw_value))
    # 1. Match currency symbol directly attached or near digits
    match = re.search(r"(?:৳|Tk\.?|BDT|\$|€|£|Rs\.?)\s*([\d,]+(?:\.\d+)?)", text, re.I)
    if match:
        try:
            val = float(match.group(1).replace(",", ""))
            if val > 0:
                return val
        except ValueError:
            pass

    # 2. Match standard formatted numbers with commas (e.g. 59,500 or 120,000)
    match_comma = re.search(r"\b([1-9]\d{0,2}(?:,\d{3})+(?:\.\d+)?)\b", text)
    if match_comma:
        try:
            val = float(match_comma.group(1).replace(",", ""))
            if val >= 50:
                return val
        except ValueError:
            pass

    return None


def clean_text(text):
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", str(text))
    return re.sub(r"\s+", " ", cleaned).strip()


class UniversalScraper:
    """Universal e-commerce scraper supporting structured metadata, heuristic DOM parsing,
    and optimized preset profiles for popular sites."""

    KNOWN_PROFILES = {
        "startech.com.bd": {
            "name": "startech.com.bd",
            "item_selector": ".p-item",
            "fields": {
                "name": {"selector": ".p-item-name a", "attr": "text"},
                "url": {"selector": ".p-item-name a", "attr": "href"},
                "price": {"selector": "meta[itemprop='price']", "attr": "content"},
                "price_text": {"selector": ".p-item-price", "attr": "text"},
                "image": {"selector": ".p-item-img img", "attr": "src"},
                "availability": {"selector": "meta[itemprop='availability']", "attr": "content"},
            },
            "spec_selectors": {
                "specifications": ".short-description li",
            },
            "detail": {
                "description": ".full-description, #specification",
                "short_description": ".short-description",
                "gallery": ".product-images img",
                "spec_table": ".data-table tr",
            },
        },
        "pickaboo.com": {
            "name": "pickaboo.com",
            "item_selector": ".product-item, .product-card",
            "fields": {
                "name": {"selector": ".product-name, .title", "attr": "text"},
                "url": {"selector": "a.product-item-link, a", "attr": "href"},
                "price": {"selector": ".special-price .price, .price", "attr": "text"},
                "image": {"selector": "img.product-image-photo, img", "attr": "src"},
            },
            "detail": {
                "description": "#product-details",
                "gallery": ".gallery-placeholder img",
            },
        },
        "techlandbd.com": {
            "name": "techlandbd.com",
            "item_selector": ".product-thumb",
            "fields": {
                "name": {"selector": ".name a", "attr": "text"},
                "url": {"selector": ".name a", "attr": "href"},
                "price": {"selector": ".price-new, .price", "attr": "text"},
                "image": {"selector": ".image img", "attr": "src"},
            },
            "detail": {
                "description": "#tab-description",
                "gallery": ".thumbnails img",
            },
        },
    }

    def __init__(self, client: EcommerceHttpClient):
        self.client = client

    def detect_preset(self, url: str):
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        for domain, profile in self.KNOWN_PROFILES.items():
            if domain in netloc:
                return profile
        return None

    def _absolutize(self, base_url: str, link: str):
        if not link:
            return None
        return urljoin(base_url, link.strip())

    def _extract_jsonld(self, tree: HTMLParser, base_url: str) -> list[dict]:
        """Extract products from schema.org JSON-LD scripts, supporting @graph structures."""
        products = []
        for script in tree.css("script[type='application/ld+json']"):
            try:
                data = json.loads(script.text())
            except Exception:
                continue

            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if "@graph" in data and isinstance(data["@graph"], list):
                    items = data["@graph"]
                else:
                    items = [data]

            for item in items:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("@type", "")
                if item_type == "Product":
                    products.append(self._parse_jsonld_product(item, base_url))
                elif item_type == "ItemList" and "itemListElement" in item:
                    for element in item["itemListElement"]:
                        sub_item = element.get("item", element) if isinstance(element, dict) else None
                        if isinstance(sub_item, dict) and sub_item.get("@type") == "Product":
                            products.append(self._parse_jsonld_product(sub_item, base_url))
        return products

    def _parse_jsonld_product(self, item: dict, base_url: str) -> dict:
        name = clean_text(item.get("name", ""))
        offers = item.get("offers", {})
        if isinstance(offers, list) and offers:
            offers = offers[0]
        price = None
        if isinstance(offers, dict):
            price = normalize_price(offers.get("price") or offers.get("lowPrice") or offers.get("highPrice"))
        availability = offers.get("availability") if isinstance(offers, dict) else "InStock"
        if availability and "/" in availability:
            availability = availability.split("/")[-1]

        image = item.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        elif isinstance(image, dict):
            image = image.get("url")

        brand = item.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")

        url = self._absolutize(base_url, item.get("url") or "")
        return {
            "name": name,
            "price": price,
            "url": url,
            "image": self._absolutize(base_url, image) if image else None,
            "brand": str(brand) if brand else None,
            "description": clean_text(item.get("description", "")),
            "availability": availability or "InStock",
            "rating": None,
            "rating_count": None,
            "specifications": {},
        }

    def _extract_heuristic_cards(self, tree: HTMLParser, base_url: str) -> list[dict]:
        """Generic DOM heuristic parser that finds repeated product cards on any e-commerce site,
        including modern headless/Next.js/Tailwind CSS frameworks."""
        products = []
        seen_urls = set()

        # Strategy 1: Find product links and ascend to find card containers
        product_anchors = tree.css(
            "a[href*='/product/'], a[href*='/product-detail/'], a[href*='/item/'], a[href*='/p/']"
        )

        for a in product_anchors:
            link = a.attributes.get("href")
            if not link:
                continue
            full_url = self._absolutize(base_url, link.strip())
            if not full_url or full_url in seen_urls:
                continue

            # Skip common non-product or pure category paths
            if any(token in full_url.lower() for token in ["/category/", "/tag/", "/brand/", "/cart", "/checkout", "/account", "/search?"]):
                continue

            # Ascend parent tree to find card container with image and/or price
            curr = a
            card = None
            for _ in range(5):
                if curr is None or curr.tag in ("body", "html"):
                    break
                text = curr.text()
                has_price = bool(re.search(r"(?:৳|Tk|BDT|\$|€|£|Rs|\b\d{1,3},\d{3}\b)", text, re.I))
                has_img = bool(curr.css_first("img"))
                if has_img and has_price:
                    card = curr
                    break
                curr = curr.parent

            if card is None:
                card = a.parent.parent if a.parent and a.parent.parent else a

            # Extract title
            title_node = card.css_first("h1, h2, h3, h4, h5, [class*='title'], [class*='name']")
            title = clean_text(title_node.text()) if title_node else ""
            if not title and card.css_first("img"):
                title = clean_text(card.css_first("img").attributes.get("alt", ""))
            if not title:
                title = clean_text(a.text())

            # Skip navigation links or buttons
            if not title or len(title) < 3 or title.lower() in ("add to cart", "buy now", "view details", "pre-order", "quick view"):
                continue

            seen_urls.add(full_url)

            # Extract image
            img_node = card.css_first("img")
            img_url = None
            if img_node:
                img_url = (
                    img_node.attributes.get("src")
                    or img_node.attributes.get("data-src")
                    or img_node.attributes.get("srcset")
                )
                if img_url and " " in img_url:
                    img_url = img_url.split(" ")[0].strip()
            full_image = self._absolutize(base_url, img_url) if img_url else None

            # Extract price
            price = None
            meta_p = card.css_first("meta[itemprop='price']")
            if meta_p and meta_p.attributes.get("content"):
                price = normalize_price(meta_p.attributes.get("content"))

            if price is None:
                price_nodes = card.css("[class*='theme'], [class*='price'], [class*='amount'], span, p")
                for pn in price_nodes:
                    classes = pn.attributes.get("class", "")
                    if "line-through" in classes or "strike" in classes:
                        continue
                    t = pn.text()
                    if any(c in t for c in ["৳", "Tk", "$", "BDT", "€", "£", "Rs"]):
                        p = normalize_price(t)
                        if p:
                            price = p
                            break

            if price is None:
                price = normalize_price(card.text())

            # Extract short specs
            specs = {}
            for li in card.css(".short-description li, ul li"):
                text = clean_text(li.text())
                if ":" in text:
                    k, v = text.split(":", 1)
                    specs[k.strip()] = v.strip()

            products.append({
                "name": title,
                "price": price,
                "url": full_url,
                "image": full_image,
                "brand": None,
                "description": "",
                "availability": "InStock",
                "rating": None,
                "rating_count": None,
                "specifications": specs,
            })

        # Strategy 2: Candidate selector nodes if Strategy 1 did not find products
        if not products:
            candidate_selectors = [
                ".p-item",
                ".product-item",
                ".product-card",
                ".product-thumb",
                ".product-box",
                "[data-product-id]",
                "[itemtype*='Product']",
                "article",
                ".col-md-3 .card",
                ".grid-item",
            ]
            nodes = []
            for selector in candidate_selectors:
                found = tree.css(selector)
                if len(found) >= 2:
                    nodes = found
                    break

            for node in nodes:
                name_el = (
                    node.css_first(".p-item-name, .product-title, .title, .name, h2, h3, h4, a[title]")
                    or (node if node.tag == "a" else node.css_first("a"))
                )
                name = clean_text(name_el.text(strip=True)) if name_el else ""
                if not name or len(name) < 3 or name.lower() in ("add to cart", "buy now", "pre-order"):
                    continue

                link_el = node if node.tag == "a" else (node.css_first("a") or name_el)
                link = link_el.attributes.get("href") if link_el else ""
                full_url = self._absolutize(base_url, link) if link else None
                if not full_url or full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                price = None
                price_meta = node.css_first("meta[itemprop='price']")
                if price_meta and price_meta.attributes.get("content"):
                    price = normalize_price(price_meta.attributes.get("content"))
                if price is None:
                    price_el = node.css_first(".price, .p-item-price, .special-price, .current-price, [class*='price']")
                    if price_el:
                        price = normalize_price(price_el.text())
                if price is None:
                    price = normalize_price(node.text())

                img_el = node.css_first("img")
                img_url = None
                if img_el:
                    img_url = (
                        img_el.attributes.get("src")
                        or img_el.attributes.get("data-src")
                        or img_el.attributes.get("srcset")
                    )
                    if img_url and " " in img_url:
                        img_url = img_url.split(" ")[0].strip()
                full_image = self._absolutize(base_url, img_url) if img_url else None

                specs = {}
                for li in node.css(".short-description li, ul li"):
                    text = clean_text(li.text())
                    if ":" in text:
                        k, v = text.split(":", 1)
                        specs[k.strip()] = v.strip()

                products.append({
                    "name": name,
                    "price": price,
                    "url": full_url,
                    "image": full_image,
                    "brand": None,
                    "description": "",
                    "availability": "InStock",
                    "rating": None,
                    "rating_count": None,
                    "specifications": specs,
                })

        return products

    async def scrape_detail_page(self, product: dict):
        """Visits product detail page to extract detailed description, images, and spec tables."""
        url = product.get("url")
        if not url:
            return product

        try:
            resp = await self.client.get(url)
            tree = HTMLParser(resp.text)

            # Check JSON-LD on detail page
            jsonld_list = self._extract_jsonld(tree, url)
            if jsonld_list:
                ld = jsonld_list[0]
                if not product.get("price") and ld.get("price"):
                    product["price"] = ld["price"]
                if not product.get("description") and ld.get("description"):
                    product["description"] = ld["description"]
                if not product.get("brand") and ld.get("brand"):
                    product["brand"] = ld["brand"]

            # Gallery images
            images = []
            for img in tree.css(".product-images img, .gallery img, #gallery img, .thumbnails img, img[itemprop='image']"):
                src = img.attributes.get("src") or img.attributes.get("data-src") or img.attributes.get("srcset")
                if src and " " in src:
                    src = src.split(" ")[0].strip()
                if src:
                    full_src = self._absolutize(url, src)
                    if full_src and full_src not in images:
                        images.append(full_src)
            product["images"] = images if images else ([product["image"]] if product.get("image") else [])

            # Description
            desc_el = tree.css_first(".full-description, #specification, #tab-description, [itemprop='description'], .product-description")
            if desc_el and not product.get("description"):
                product["description"] = clean_text(desc_el.text())

            # Specs table & Tailwind flex key-value rows
            specs = dict(product.get("specifications") or {})
            for row in tree.css("table.data-table tr, table.specification tr, table tr"):
                tds = row.css("td, th")
                if len(tds) >= 2:
                    k = clean_text(tds[0].text())
                    v = clean_text(tds[1].text())
                    if k and v:
                        specs[k] = v

            # Modern flex key-value rows (as in Tailwind/Next.js e-commerce)
            for row in tree.css("[id*='spec'] .flex.items-start, [class*='spec'] .flex.items-start"):
                cols = []
                c = row.child
                while c:
                    if c.tag == "div":
                        cols.append(c)
                    c = c.next
                if len(cols) >= 2:
                    k = clean_text(cols[0].text())
                    v = clean_text(cols[1].text())
                    if k and v and len(k) < 60 and len(v) < 300:
                        specs[k] = v

            product["specifications"] = specs

        except Exception as e:
            product["detail_error"] = str(e)

        return product

    async def scrape(self, target_url: str, max_products: int = 40) -> tuple[list[dict], str]:
        """Universal scraper entrypoint: handles preset profiles and universal heuristic fallback."""
        parsed = urlparse(target_url)
        domain = parsed.netloc.lower()

        print(f"[FETCH] Target URL: {target_url} (Domain: {domain})")
        resp = await self.client.get(target_url)
        tree = HTMLParser(resp.text)

        preset = self.detect_preset(target_url)
        products = []

        if preset:
            print(f"[PRESET] Detected preset accelerator profile for: {domain}")
            # Try preset card parsing
            for node in tree.css(preset["item_selector"]):
                fields = preset["fields"]
                name_el = node.css_first(fields["name"]["selector"]) if "name" in fields else None
                name = clean_text(name_el.text()) if name_el else ""
                if not name:
                    continue

                url_el = node.css_first(fields["url"]["selector"]) if "url" in fields else None
                link = url_el.attributes.get("href") if url_el else ""
                full_url = self._absolutize(target_url, link)

                price = None
                if "price" in fields:
                    p_el = node.css_first(fields["price"]["selector"])
                    if p_el:
                        price = normalize_price(p_el.attributes.get(fields["price"].get("attr", "content")) or p_el.text())
                if price is None and "price_text" in fields:
                    pt_el = node.css_first(fields["price_text"]["selector"])
                    if pt_el:
                        price = normalize_price(pt_el.text())

                img_el = node.css_first(fields["image"]["selector"]) if "image" in fields else None
                img = img_el.attributes.get("src") if img_el else None
                full_image = self._absolutize(target_url, img) if img else None

                specs = {}
                for spec_key, spec_sel in preset.get("spec_selectors", {}).items():
                    for li in node.css(spec_sel):
                        t = clean_text(li.text())
                        if ":" in t:
                            k, v = t.split(":", 1)
                            specs[k.strip()] = v.strip()

                products.append({
                    "name": name,
                    "price": price,
                    "url": full_url,
                    "image": full_image,
                    "brand": None,
                    "description": "",
                    "availability": "InStock",
                    "rating": None,
                    "rating_count": None,
                    "specifications": specs,
                })

        # If preset returned no products or not a preset site, run Universal Structured + Heuristic Extractor
        if not products:
            print("[EXTRACT] Running Universal Structured Metadata (JSON-LD) extraction...")
            products = self._extract_jsonld(tree, target_url)

        if not products:
            print("[EXTRACT] Running Universal DOM Card & Price Heuristic extraction...")
            products = self._extract_heuristic_cards(tree, target_url)

        print(f"[EXTRACT] Discovered {len(products)} initial product listings.")

        # Limit to max_products to keep mining cycle fast and responsive
        records_to_enrich = products[:max_products]

        # Concurrently fetch detail pages (controlled with semaphore)
        semaphore = asyncio.Semaphore(4)

        async def _fetch(p):
            async with semaphore:
                return await self.scrape_detail_page(p)

        print(f"[ENRICH] Encompassing detail pages for top {len(records_to_enrich)} products...")
        enriched = await asyncio.gather(*[_fetch(p) for p in records_to_enrich])

        # Generate slug and tag source domain
        for rec in enriched:
            name_slug = re.sub(r"[^a-z0-9]+", "-", rec["name"].lower()).strip("-")
            rec["productSlug"] = f"{name_slug[:50]}-{abs(hash(rec.get('url') or rec['name'])) % 100000}"
            rec["source_domain"] = domain
            rec["source_url"] = target_url

        return enriched, domain
