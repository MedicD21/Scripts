import requests
from bs4 import BeautifulSoup
import csv
import tqdm
import argparse
import re
from pathlib import Path
from time import sleep
from urllib.parse import parse_qs, unquote, urlparse
from ddgs import DDGS


USER_AGENT = (
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
	"AppleWebKit/537.36 (KHTML, like Gecko) "
	"Chrome/122.0.0.0 Safari/537.36"
)

EXCLUDED_DOMAINS = {
	"facebook.com",
	"instagram.com",
	"yelp.com",
	"yellowpages.com",
	"bbb.org",
	"mapquest.com",
	"tripadvisor.com",
	"linkedin.com",
	"manta.com",
	"doordash.com",
	"ubereats.com",
	"grubhub.com",
	"wikipedia.org",
	"tiktok.com",
	"whatnow.com",
	"thisismytake.com",
	"wheree.com",
	"weeblyte.com",
}

GENERIC_TOKENS = {
	"the",
	"and",
	"of",
	"llc",
	"inc",
	"co",
	"company",
	"restaurant",
	"kitchen",
	"cafe",
	"shop",
	"columbus",
	"ohio",
}


def read_business_names(input_path: Path) -> list[str]:
	if not input_path.exists():
		raise FileNotFoundError(f"Input file not found: {input_path}")

	names: list[str] = []

	if input_path.suffix.lower() == ".csv":
		with input_path.open("r", encoding="utf-8-sig", newline="") as file:
			reader = csv.reader(file)
			for row in reader:
				if not row:
					continue
				name = row[0].strip()
				if name and name.lower() not in {"business", "business name", "name"}:
					names.append(name)
	else:
		with input_path.open("r", encoding="utf-8") as file:
			for line in file:
				name = line.strip()
				if name:
					names.append(name)

	return names


def extract_real_url(url: str) -> str:
	if not url:
		return ""

	if "duckduckgo.com/l/?" in url:
		parsed = urlparse(url)
		query = parse_qs(parsed.query)
		if "uddg" in query and query["uddg"]:
			return unquote(query["uddg"][0])

	return url


def normalize_domain(url: str) -> str:
	parsed = urlparse(url)
	domain = parsed.netloc.lower().replace("www.", "")
	return domain


def normalize_text(value: str) -> str:
	value = value.lower()
	value = value.replace("&", " and ")
	return re.sub(r"[^a-z0-9]+", " ", value).strip()


def business_tokens(name: str) -> list[str]:
	tokens = normalize_text(name).split()
	filtered = [token for token in tokens if token not in GENERIC_TOKENS and len(token) > 1]
	return filtered or tokens


def is_excluded(url: str) -> bool:
	domain = normalize_domain(url)
	return any(domain.endswith(blocked) for blocked in EXCLUDED_DOMAINS)


def search_duckduckgo(query: str, timeout: int = 20) -> list[str]:
	endpoint = "https://duckduckgo.com/html/"
	headers = {"User-Agent": USER_AGENT}
	params = {"q": query}

	response = requests.get(endpoint, headers=headers, params=params, timeout=timeout)
	response.raise_for_status()

	soup = BeautifulSoup(response.text, "html.parser")
	links: list[str] = []

	for anchor in soup.select("a.result__a"):
		href = anchor.get("href", "").strip()
		if not href:
			continue

		real_url = extract_real_url(href)
		if real_url.startswith("http"):
			links.append(real_url)

	return links


def search_bing(query: str, timeout: int = 20) -> list[str]:
	endpoint = "https://www.bing.com/search"
	headers = {"User-Agent": USER_AGENT}
	params = {"q": query}

	response = requests.get(endpoint, headers=headers, params=params, timeout=timeout)
	response.raise_for_status()

	soup = BeautifulSoup(response.text, "html.parser")
	links: list[str] = []

	for anchor in soup.select("li.b_algo h2 a"):
		href = anchor.get("href", "").strip()
		if href.startswith("http"):
			links.append(href)

	return links


def search_ddgs(query: str, max_results: int = 12) -> list[dict[str, str]]:
	results: list[dict[str, str]] = []
	with DDGS(timeout=10) as ddgs:
		for item in ddgs.text(query, max_results=max_results):
			url = (item.get("href") or "").strip()
			if not url.startswith("http"):
				continue
			results.append(
				{
					"url": url,
					"title": (item.get("title") or "").strip(),
					"body": (item.get("body") or "").strip(),
				}
			)
	return results


def business_name_variants(name: str) -> list[str]:
	variants = {name.strip()}

	if "/" in name:
		parts = [part.strip() for part in name.split("/") if part.strip()]
		variants.update(parts)
		variants.add(" ".join(parts))

	if "&" in name:
		variants.add(name.replace("&", "and"))

	# Remove apostrophes for search fallback
	variants.add(name.replace("'", ""))

	cleaned = []
	for variant in variants:
		v = " ".join(variant.split())
		if v:
			cleaned.append(v)

	return cleaned


def score_candidate(name: str, url: str, title: str = "", body: str = "") -> int:
	if is_excluded(url):
		return -999

	tokens = business_tokens(name)
	if not tokens:
		return -999

	domain = normalize_domain(url)
	path = urlparse(url).path.lower()
	combined = f"{normalize_text(title)} {normalize_text(body)}"

	score = 0
	domain_token_match = False
	for token in tokens:
		if token in domain:
			score += 5
			domain_token_match = True
		if token in path:
			score += 3
		if token in combined:
			score += 2

	if not domain_token_match:
		score -= 10

	if "official" in combined:
		score += 2
	if "columbus" in combined or "ohio" in combined:
		score += 1

	# Penalize general info pages and social profiles that slip through
	if any(word in domain for word in ("wikipedia", "tripadvisor", "reddit", "x.com", "twitter")):
		score -= 8

	return score


def choose_best_candidate(name: str, candidates: list[dict[str, str]]) -> str:
	if not candidates:
		return "NOT_FOUND"

	scored: list[tuple[int, str]] = []
	for candidate in candidates:
		url = candidate.get("url", "")
		title = candidate.get("title", "")
		body = candidate.get("body", "")
		scored.append((score_candidate(name, url, title, body), url))

	best_score, best_url = max(scored, key=lambda entry: entry[0])
	if best_score < 4:
		return "NOT_FOUND"

	parsed = urlparse(best_url)
	if parsed.scheme and parsed.netloc:
		return f"{parsed.scheme}://{parsed.netloc}/"

	return best_url


def best_website_for_business(name: str) -> str:
	queries: list[str] = []
	for variant in business_name_variants(name):
		queries.extend(
			[
				f"{variant} Columbus Ohio official website",
				f"{variant} Columbus OH",
				f"{variant} menu Columbus Ohio",
			]
		)

	# Deduplicate while preserving order
	queries = list(dict.fromkeys(queries))
	all_candidates: list[dict[str, str]] = []

	for query in queries:
		try:
			ddgs_results = search_ddgs(query, max_results=8)
			all_candidates.extend(ddgs_results)
		except Exception:
			pass

		# Keep HTML scrapers as tertiary fallback
		for search_fn in (search_duckduckgo, search_bing):
			try:
				links = search_fn(query)
			except requests.RequestException:
				continue
			for link in links:
				all_candidates.append({"url": link, "title": "", "body": ""})

		sleep(0.1)

	# Deduplicate by URL while preserving order
	seen: set[str] = set()
	unique_candidates: list[dict[str, str]] = []
	for item in all_candidates:
		url = item.get("url", "")
		if not url or url in seen:
			continue
		seen.add(url)
		unique_candidates.append(item)

	best = choose_best_candidate(name, unique_candidates)
	if best != "NOT_FOUND":
		return best

	return "NOT_FOUND"


def write_results(output_path: Path, results: list[tuple[str, str]]) -> None:
	with output_path.open("w", encoding="utf-8", newline="") as file:
		writer = csv.writer(file)
		writer.writerow(["Business Name", "Website"])
		writer.writerows(results)


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Find websites for a list of businesses in Columbus, Ohio."
	)
	parser.add_argument(
		"--input",
		required=True,
		help="Path to input file (.txt or .csv). One business name per line or first CSV column.",
	)
	parser.add_argument(
		"--output",
		default="business_websites_columbus.csv",
		help="Path to output CSV file.",
	)
	args = parser.parse_args()

	input_path = Path(args.input)
	output_path = Path(args.output)

	business_names = read_business_names(input_path)
	if not business_names:
		raise ValueError("No business names found in input file.")

	results: list[tuple[str, str]] = []
	for business_name in tqdm.tqdm(business_names, desc="Searching businesses"):
		website = best_website_for_business(business_name)
		results.append((business_name, website))

	write_results(output_path, results)
	print(f"Saved {len(results)} rows to {output_path}")


if __name__ == "__main__":
	main()

