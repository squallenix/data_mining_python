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


def is_valid_product_image(url: str) -> bool:
    """Rejects SVG icons, UI badges, tracking pixels, logos, and promotional hero banners."""
    if not url:
        return False
    u = str(url).strip().lower()

    if u.startswith("data:") or "1x1" in u or "pixel" in u or "spacer" in u:
        return False

    if u.endswith(".svg") or any(t in u for t in [
        "/images/logo/", "/footer/", "photos.svg", "customer.svg", "icon", "badge",
        "payment", "wishlist", "cart.svg", "star.svg", "rating.svg", "spinner", "loader"
    ]):
        return False

    banner_tokens = [
        "home+banner", "home-banner", "herobanner", "hero-banner", "main-slider",
        "slider-banner", "advertisement", "promo-banner", "1920x570", "1920x", "banner/"
    ]
    if any(token in u for token in banner_tokens):
        return False

    valid_exts = (".jpg", ".jpeg", ".png", ".webp", ".avif")
    is_cdn = any(cdn in u for cdn in ["cdn.shopify", "cloudinary", "img.", "/image/", "/storage/", "/products/", "/media/", "/upload/"])
    if not any(u.split("?")[0].endswith(ext) for ext in valid_exts) and not is_cdn:
        return False

    return True


def extract_node_image(node) -> str | None:
    """Extracts the cleanest, highest-resolution product image from an element or img node,
    supporting lazy-load attributes, srcset, data-zoom, and thumbnail upscaling."""
    if not node:
        return None

    img = node if getattr(node, "tag", None) == "img" else node.css_first("img")
    if not img:
        return None

    candidates = [
        img.attributes.get("data-zoom-image"),
        img.attributes.get("data-zoom"),
        img.attributes.get("data-high-res"),
        img.attributes.get("data-original"),
        img.attributes.get("data-lazy-src"),
        img.attributes.get("data-lazy"),
        img.attributes.get("data-src"),
        img.attributes.get("data-srcset"),
        img.attributes.get("srcset"),
        img.attributes.get("src"),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        url = candidate.strip()
        if "," in url:
            parts = [p.strip().split(" ")[0].strip() for p in url.split(",") if p.strip()]
            for part in reversed(parts):
                if is_valid_product_image(part):
                    return part
        elif " " in url:
            first = url.split(" ")[0].strip()
            if is_valid_product_image(first):
                return first
        elif is_valid_product_image(url):
            return url

    return None


def upgrade_image_resolution(url: str) -> str:
    """Automatically converts tiny thumbnail URLs to high-resolution display images."""
    if not url:
        return url

    if "cdn.shopify.com" in url or "/cdn/shop/" in url:
        return re.sub(r"_(?:50x50|100x100|150x150|small|thumb|compact)\.", "_800x800.", url)

    if "startech.com.bd" in url or "/image/cache/" in url:
        return re.sub(r"-\d+x\d+(\.\w+)$", r"-500x500\1", url)

    return re.sub(r"-\d{2,4}x\d{2,4}(\.(?:jpg|jpeg|png|webp))$", r"\1", url, flags=re.I)


def is_valid_product_name(name: str) -> bool:
    """Rejects non-product headings like 'slide 1', 'banner', 'view details'."""
    if not name or len(name.strip()) < 4:
        return False
    n = name.strip().lower()
    junk = [
        "slide", "slide 1", "slide 2", "slide 3", "slide 4", "banner", "hero",
        "view all", "view details", "read more", "learn more", "add to cart",
        "buy now", "pre-order", "shop now", "see more", "terms & conditions",
        "privacy policy", "contact us", "about us", "quick view"
    ]
    if n in junk or re.match(r"^slide\s*\d+$", n):
        return False
    return True


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
            "item_selector": "a[href*='/product-detail/'], .product-item, .product-card",
            "fields": {
                "name": {"selector": "h3, [class*='title'], .product-name", "attr": "text"},
                "url": {"selector": "self", "attr": "href"},
                "price": {"selector": "span[class*='text-blue'], .special-price, .price, [class*='price']", "attr": "text"},
                "image": {"selector": "img", "attr": "src"},
            },
            "detail": {
                "description": "#product-details, [data-slot='card-content'], .description",
                "gallery": ".gallery-placeholder img, [class*='product-media'] img",
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
        image_url = None
        if isinstance(image, list):
            for img in image:
                cand = img.get("url") if isinstance(img, dict) else str(img)
                if is_valid_product_image(cand):
                    image_url = cand
                    break
        elif isinstance(image, dict):
            cand = image.get("url")
            if is_valid_product_image(cand):
                image_url = cand
        elif isinstance(image, str) and is_valid_product_image(image):
            image_url = image

        final_image = upgrade_image_resolution(self._absolutize(base_url, image_url)) if image_url else None

        brand = item.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")

        url = self._absolutize(base_url, item.get("url") or "")
        return {
            "name": name,
            "price": price,
            "url": url,
            "image": final_image,
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

            # Skip navigation links, buttons, and slide banners
            if not is_valid_product_name(title):
                continue

            seen_urls.add(full_url)

            # Extract image (prioritizing real product image over badges/icons)
            img_url = None
            for img_el in card.css("img"):
                cand = extract_node_image(img_el)
                if cand and is_valid_product_image(cand):
                    img_url = cand
                    break
            full_image = upgrade_image_resolution(self._absolutize(base_url, img_url)) if img_url else None

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
                if not is_valid_product_name(name):
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

                img_url = None
                for img_el in node.css("img"):
                    cand = extract_node_image(img_el)
                    if cand and is_valid_product_image(cand):
                        img_url = cand
                        break
                full_image = upgrade_image_resolution(self._absolutize(base_url, img_url)) if img_url else None

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

            # Detail page meta tags for price
            if not product.get("price"):
                for meta_sel in [
                    "meta[name='product:price:amount']",
                    "meta[name='og:price:amount']",
                    "meta[property='product:price:amount']",
                    "meta[property='og:price:amount']",
                    "meta[itemprop='price']",
                ]:
                    m_el = tree.css_first(meta_sel)
                    if m_el and m_el.attributes.get("content"):
                        p_val = normalize_price(m_el.attributes.get("content"))
                        if p_val and p_val > 0:
                            product["price"] = p_val
                            break

            # Detail page DOM price elements
            if not product.get("price"):
                for p_node in tree.css(".price, .special-price, .current-price, [class*='price'], [class*='amount'], span, p"):
                    classes = p_node.attributes.get("class", "")
                    if "line-through" in classes or "strike" in classes or p_node.tag == "s":
                        continue
                    if p_node.parent and (p_node.parent.tag == "s" or "line-through" in p_node.parent.attributes.get("class", "")):
                        continue
                    t = p_node.text(strip=True)
                    if any(c in t for c in ["৳", "Tk", "$", "BDT"]):
                        p_val = normalize_price(t)
                        if p_val and p_val > 0:
                            product["price"] = p_val
                            break

            # Gallery images: only accept real, high-resolution product photos
            gallery_images = []
            for img_el in tree.css(".product-images img, .gallery img, #gallery img, .thumbnails img, img[itemprop='image'], .product-media img, .product-gallery img"):
                src = extract_node_image(img_el)
                if src and is_valid_product_image(src):
                    full_src = upgrade_image_resolution(self._absolutize(url, src))
                    if full_src and full_src not in gallery_images:
                        gallery_images.append(full_src)

            # Preserve valid listing image if gallery didn't find anything
            if gallery_images:
                product["images"] = gallery_images
                product["image"] = gallery_images[0]
            elif product.get("image") and is_valid_product_image(product.get("image")):
                product["images"] = [product["image"]]
            else:
                product["images"] = []

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
                if not is_valid_product_name(name):
                    continue

                if fields.get("url", {}).get("selector") == "self":
                    url_el = node if getattr(node, "tag", None) == "a" else (node.css_first("a") or node)
                else:
                    url_el = node.css_first(fields["url"]["selector"]) if "url" in fields else None
                link = url_el.attributes.get("href") if url_el else ""
                full_url = self._absolutize(target_url, link)
                if not full_url:
                    continue

                price = None
                if "price" in fields:
                    p_el = node.css_first(fields["price"]["selector"])
                    if p_el:
                        price = normalize_price(p_el.attributes.get(fields["price"].get("attr", "content")) or p_el.text())
                if price is None and "price_text" in fields:
                    pt_el = node.css_first(fields["price_text"]["selector"])
                    if pt_el:
                        price = normalize_price(pt_el.text())
                if price is None:
                    for p_el in node.css("span, div, p"):
                        classes = p_el.attributes.get("class", "")
                        if "line-through" in classes or "strike" in classes or p_el.tag == "s":
                            continue
                        if p_el.parent and (p_el.parent.tag == "s" or "line-through" in p_el.parent.attributes.get("class", "")):
                            continue
                        txt = p_el.text(strip=True)
                        if any(c in txt for c in ["৳", "Tk", "$", "BDT"]):
                            p = normalize_price(txt)
                            if p and p > 0:
                                price = p
                                break

                img_node = node.css_first(fields["image"]["selector"]) if "image" in fields else node.css_first("img")
                raw_img = extract_node_image(img_node)
                full_image = upgrade_image_resolution(self._absolutize(target_url, raw_img)) if raw_img else None

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

        # Check DOM heuristic cards
        dom_products = self._extract_heuristic_cards(tree, target_url)

        if not products:
            print("[EXTRACT] Running Universal DOM Card & Price Heuristic extraction...")
            products = dom_products
        elif dom_products:
            # If JSON-LD products have missing prices or images, enrich them from matching DOM cards
            dom_by_url = {re.sub(r"https?://(www\.)?", "", p.get("url") or "").rstrip("/"): p for p in dom_products if p.get("url")}
            dom_by_name = {p["name"].lower()[:25]: p for p in dom_products if p.get("name")}
            for p in products:
                norm_u = re.sub(r"https?://(www\.)?", "", p.get("url") or "").rstrip("/")
                match = dom_by_url.get(norm_u) or dom_by_name.get(p.get("name", "").lower()[:25])
                if match:
                    if not p.get("price") and match.get("price"):
                        p["price"] = match["price"]
                    if not p.get("image") and match.get("image"):
                        p["image"] = match["image"]

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
