"""Pizza Spend Map build: download, clean, score, write docs/data.geojson.

python3 scripts/build.py                       # use cached raw files, fetch only what is missing
python3 scripts/build.py --refresh-competitors # re-pull FSA and OpenStreetMap (monthly Action)
"""
import csv, datetime, hashlib, io, json, re, sys, time, urllib.parse, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, DOCS = ROOT / "data" / "raw", ROOT / "docs"
FETCHED = RAW / "fetched.json"
UA = {"User-Agent": "pizza-spend-map/1.0 (public research; github.com/AnkurNapa/pizza-spend-map)"}

# name: (FSA local authority id, region, ring). Ring drives the "outer London and Essex" default view.
AREAS = {
    "Newham": (112, "London", "Inner London"), "Tower Hamlets": (117, "London", "Inner London"),
    "Hackney": (99, "London", "Inner London"), "Waltham Forest": (118, "London", "Outer London"),
    "Redbridge": (113, "London", "Outer London"), "Barking and Dagenham": (88, "London", "Outer London"),
    "Havering": (103, "London", "Outer London"), "Thurrock": (47, "East of England", "Essex"),
    "Basildon": (6, "East of England", "Essex"), "Southend-on-Sea": (46, "East of England", "Essex"),
    "Brentwood": (8, "East of England", "Essex"), "Chelmsford": (10, "East of England", "Essex"),
    "Harlow": (13, "East of England", "Essex"), "Epping Forest": (12, "East of England", "Essex"),
    "Castle Point": (9, "East of England", "Essex"),
}
CHAINS = {"Domino's": r"domino", "Papa John's": r"papa\s*john", "Pizza Hut": r"pizza\s*hut"}
CENSUS = ["ts001", "ts003", "ts006", "ts007a", "ts044", "ts054", "ts066"]
ARC = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
URLS = {
    "census": "https://www.nomisweb.co.uk/output/census/2021/census2021-{t}.zip",
    "boundaries": ARC + "/MSOA_2021_EW_BFE_V8_RUC/FeatureServer/3",
    "lookup": ARC + "/OA_LSOA_MSOA_EW_DEC_2021_LU_v3/FeatureServer/0",
    "income": "https://www.ons.gov.uk/file?uri=/employmentandlabourmarket/peopleinwork/earningsandworkinghours/datasets/smallareaincomeestimatesformiddlelayersuperoutputareasenglandandwales/financialyearending2023/datasetfinal.xlsx",
    "imd": "https://assets.publishing.service.gov.uk/media/691ded56d140bbbaa59a2a7d/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv",
    "fsa": "https://api.ratings.food.gov.uk/Establishments",
    "overpass": "https://overpass-api.de/api/interpreter",
    "schools": "https://ea-edubase-api-prod.azurewebsites.net/edubase/downloads/public/edubasealldata{d}.csv",
}


def get(url, data=None, headers=None, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
            return urllib.request.urlopen(req, timeout=180).read()
        except Exception as e:  # network blips are common on Overpass and the FSA API
            if i == tries - 1:
                raise RuntimeError(f"fetch failed: {url[:90]}: {e}")
            time.sleep(5 * (i + 1))


def cached(path, fetch, refresh=False):
    """Return bytes of a raw file, fetching it only when missing or refresh is asked."""
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fetch())
        log = json.loads(FETCHED.read_text()) if FETCHED.exists() else {}
        FETCHED.write_text(json.dumps({**log, str(path.relative_to(RAW)): datetime.date.today().isoformat()}, indent=1, sort_keys=True))
    return path.read_bytes()


def fetched_on(path):
    """Download date from fetched.json (file times change on git checkout)."""
    log = json.loads(FETCHED.read_text()) if FETCHED.exists() else {}
    return log.get(str(path.relative_to(RAW))) or datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()


# ---------- demand ----------
def load_census():
    rows = {}
    for t in CENSUS:
        p = RAW / "census" / f"census2021-{t}-msoa.csv"
        if not p.exists():
            z = zipfile.ZipFile(io.BytesIO(get(URLS["census"].format(t=t))))
            cached(p, lambda: z.read(f"census2021-{t}-msoa.csv"))
        for r in csv.DictReader(io.StringIO(p.read_text(encoding="utf-8-sig"))):
            rows.setdefault(r["geography code"], {}).update({f"{t}|{k}": v for k, v in r.items()})
    out = {}
    for code, r in rows.items():
        def v(t, start):
            label = lambda k: k[len(t) + 1:].replace("; measures: Value", "")
            ks = [k for k in r if k.startswith(f"{t}|") and label(k) == start.rstrip(";")]
            ks = ks or [k for k in r if k.startswith(f"{t}|") and label(k).startswith(start)]
            assert len(ks) == 1, (t, start, ks)
            return float(r[ks[0]])
        age = lambda a: v("ts007a", f"Age: Aged {a}")
        hh = v("ts003", "Household composition: Total")
        pop = v("ts001", "Residence type: Total")
        out[code] = {
            "population": pop,
            "density": v("ts006", "Population Density"),
            "age16_34": (age("15 to 19") * 4 / 5 + age("20 to 24") + age("25 to 29") + age("30 to 34")) / pop,
            "students": (v("ts066", "Economic activity status: Economically active and a full-time student")
                         + v("ts066", "Economic activity status: Economically inactive: Student")) / pop,
            "kids_hh": sum(v("ts003", "Household composition: " + s) for s in [
                "Single family household: Married or civil partnership couple: Dependent children",
                "Single family household: Cohabiting couple family: With dependent children",
                "Single family household: Lone parent family: With dependent children",
                "Other household types: With dependent children"]) / hh,
            "one_person_hh": v("ts003", "Household composition: One person household;") / hh,
            "flats": (v("ts044", "Accommodation type: In a purpose-built block") + v("ts044", "Accommodation type: Part of a converted")
                      + v("ts044", "Accommodation type: Part of another converted") + v("ts044", "Accommodation type: In a commercial")) / v("ts044", "Accommodation type: Total"),
            "renters": (v("ts054", "Tenure of household: Social rented") + v("ts054", "Tenure of household: Private rented")) / v("ts054", "Tenure of household: Total"),
            "households": hh,
        }
    return out


# ---------- ability to spend ----------
def load_income():
    import openpyxl
    p = RAW / "income" / "saie_fye2023.xlsx"
    cached(p, lambda: get(URLS["income"]))
    ws = openpyxl.load_workbook(p, read_only=True)["Net annual income"]
    return {r[0]: float(r[6]) for r in ws.iter_rows(min_row=5, values_only=True) if r[0] and str(r[0]).startswith(("E02", "W02"))}


def load_lookup():
    p = RAW / "lsoa_msoa_lookup.json"
    def fetch():
        w = "LAD22NM IN (" + ",".join(f"'{a}'" for a in AREAS) + ")"
        out, off = {}, 0
        while True:
            q = urllib.parse.urlencode({"where": w, "outFields": "LSOA21CD,MSOA21CD", "returnDistinctValues": "true",
                                        "returnGeometry": "false", "orderByFields": "LSOA21CD", "resultOffset": off,
                                        "resultRecordCount": 1000, "f": "json"})
            d = json.loads(get(URLS["lookup"] + "/query?" + q))
            out.update({f["attributes"]["LSOA21CD"]: f["attributes"]["MSOA21CD"] for f in d["features"]})
            off += len(d["features"])
            if not d.get("exceededTransferLimit"):
                return json.dumps(out).encode()
    return json.loads(cached(p, fetch))


def load_imd(lookup):
    """Income domain rate per LSOA, population weighted up to MSOA."""
    p = RAW / "imd" / "File_7_IoD2025.csv"
    cached(p, lambda: get(URLS["imd"]))
    acc = {}
    for r in csv.DictReader(io.StringIO(p.read_text(encoding="utf-8-sig"))):
        m = lookup.get(r["LSOA code (2021)"])
        if m:
            pop = float(r["Total population: mid 2022"])
            a = acc.setdefault(m, [0.0, 0.0])
            a[0] += float(r["Income Score (rate)"]) * pop
            a[1] += pop
    return {m: s / n for m, (s, n) in acc.items()}


def load_policy_and_spend():
    p = RAW / "policy_and_spend.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ---------- competition ----------
def fetch_fsa():
    keep = {"Takeaway/sandwich shop", "Restaurant/Cafe/Canteen", "Mobile caterer"}
    out = {}
    for area, (la, _, _) in AREAS.items():
        for term in ["pizza", "domino", "papa john"]:
            q = urllib.parse.urlencode({"name": term, "localAuthorityId": la, "pageSize": 5000})
            d = json.loads(get(f"{URLS['fsa']}?{q}", headers={"x-api-version": "2", "Accept": "application/json"}))
            for e in d["establishments"]:
                if e["BusinessType"] in keep:
                    out[e["FHRSID"]] = {k: e.get(k) for k in ["FHRSID", "BusinessName", "BusinessType", "AddressLine1", "AddressLine2",
                                                             "AddressLine3", "PostCode", "RatingValue", "RatingDate", "LocalAuthorityName", "geocode"]}
            time.sleep(0.3)
    return json.dumps(list(out.values()), indent=0).encode()


def fetch_osm(bbox):
    s, w, n, e = bbox
    q = f"""[out:json][timeout:120];(
      nwr["amenity"~"^(fast_food|restaurant)$"]["cuisine"~"pizza",i]({s},{w},{n},{e});
      nwr["amenity"~"^(fast_food|restaurant)$"]["name"~"pizza|domino|papa ?john",i]({s},{w},{n},{e}););out center tags;"""
    # curl, not urllib: macOS system Python fails the TLS handshake with overpass-api.de
    import subprocess
    out = subprocess.run(["curl", "-sSf", "--retry", "6", "--retry-delay", "30", "--retry-all-errors", "--max-time", "300", "-A", UA["User-Agent"], "--data-urlencode", f"data={q}",
                          URLS["overpass"]], capture_output=True, check=True).stdout
    assert json.loads(out)["elements"], "Overpass returned no pizza places"
    return out


def geocode_postcodes(pcs):
    """postcodes.io bulk lookup for FSA rows that carry no coordinates."""
    out = {}
    pcs = sorted({p for p in pcs if p})
    for i in range(0, len(pcs), 100):
        d = json.loads(get("https://api.postcodes.io/postcodes", data=json.dumps({"postcodes": pcs[i:i + 100]}).encode(),
                           headers={"Content-Type": "application/json"}))
        for r in d["result"]:
            if r["result"]:
                out[r["query"]] = (r["result"]["latitude"], r["result"]["longitude"])
    return out


def norm(name):
    name = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace("'", ""))
    return {w for w in name.split() if w not in {"pizza", "pizzas", "pizzeria", "the", "ltd", "limited", "and", "takeaway", "uk", "co"}}


def chain_of(name):
    return next((c for c, rx in CHAINS.items() if re.search(rx, name or "", re.I)), None)


def merge_competitors(fsa, osm):
    """One record per outlet. An OSM point within 120 m of an FSA outlet with a shared name word (or same chain) is the same place."""
    from math import cos, radians
    pcs = geocode_postcodes([f["PostCode"] for f in fsa if not (f.get("geocode") or {}).get("latitude")])
    recs = []
    for f in fsa:
        g = f.get("geocode") or {}
        lat, lon = (float(g["latitude"]), float(g["longitude"])) if g.get("latitude") else pcs.get(f["PostCode"], (None, None))
        if lat is None:
            continue
        addr = ", ".join(x for x in [f.get("AddressLine1"), f.get("AddressLine2"), f.get("AddressLine3"), f.get("PostCode")] if x)
        recs.append({"name": f["BusinessName"], "lat": lat, "lon": lon, "postcode": f.get("PostCode"), "address": addr,
                     "rating": f["RatingValue"], "rating_date": (f.get("RatingDate") or "")[:10], "fsa_type": f["BusinessType"],
                     "fhrsid": f["FHRSID"], "osm": None, "source": "FSA"})
    for el in osm:
        t = el.get("tags", {})
        lat, lon = el.get("lat") or el.get("center", {}).get("lat"), el.get("lon") or el.get("center", {}).get("lon")
        if lat is None:
            continue
        name, osm_id = t.get("name") or t.get("brand") or "Unnamed pizza place", f"{el['type']}/{el['id']}"
        best = None
        for r in recs:
            d = ((r["lat"] - lat) * 111320) ** 2 + ((r["lon"] - lon) * 111320 * cos(radians(lat))) ** 2
            same = (norm(r["name"]) & norm(name)) or (chain_of(r["name"]) and chain_of(r["name"]) == chain_of(name))
            if d < 120 ** 2 and same and (best is None or d < best[0]):
                best = (d, r)
        if best:
            best[1]["osm"], best[1]["source"] = osm_id, "FSA + OSM"
        else:
            recs.append({"name": name, "lat": lat, "lon": lon, "postcode": t.get("addr:postcode"), "address": t.get("addr:street"),
                         "rating": None, "rating_date": None, "fsa_type": None, "fhrsid": None, "osm": osm_id, "source": "OSM"})
    for r in recs:
        r["chain"] = chain_of(r["name"])
    return recs


def outlet_weight(rating):
    """How much one outlet counts as competition. Strong hygiene (4, 5) or unknown counts fully; weak (0 to 2) counts half."""
    return 0.5 if str(rating) in {"0", "1", "2"} else 1.0


# ---------- constraints ----------
def load_schools(policy):
    from pyproj import Transformer
    p = RAW / "schools" / "edubase_latest.csv"
    def fetch():
        for back in range(0, 10):
            d = (datetime.date.today() - datetime.timedelta(days=back)).strftime("%Y%m%d")
            try:
                return get(URLS["schools"].format(d=d), tries=1)
            except RuntimeError:
                continue
        raise RuntimeError("no GIAS file in the last 10 days")
    raw = cached(p, fetch).decode("cp1252")
    rules = {r["council"]: r for r in policy.get("takeaway_school_rules", [])}
    tf = Transformer.from_crs(27700, 4326, always_xy=True)
    out = []
    for r in csv.DictReader(io.StringIO(raw)):
        la = r["LA (name)"]
        if la not in AREAS or r["EstablishmentStatus (name)"] != "Open" or r["PhaseOfEducation (name)"] not in {"Secondary", "All-through"}:
            continue
        if not r["Easting"]:
            continue
        lon, lat = tf.transform(float(r["Easting"]), float(r["Northing"]))
        rule = rules.get(la, {})
        local = rule.get("applies") is True
        # NPPF (Aug 2026) HC5 refuses takeaways within about 400 m of schools everywhere outside town centres,
        # so every school gets at least 400 m; a local or London Plan rule is marked on top of that
        out.append({"urn": r["URN"], "name": r["EstablishmentName"], "la": la, "postcode": r["Postcode"],
                    "lat": round(lat, 5), "lon": round(lon, 5), "e": float(r["Easting"]), "n": float(r["Northing"]),
                    "rule": "local" if local else "national", "radius": max(rule.get("distance_m") or 0, 400)})
    return out


# ---------- scoring ----------
def pct_rank(values):
    """0 to 100 percentile rank, ties share their average rank. None stays None."""
    idx = sorted((v, i) for i, v in enumerate(values) if v is not None)
    out, n, k = [None] * len(values), len(idx), 0
    while k < n:
        j = k
        while j + 1 < n and idx[j + 1][0] == idx[k][0]:
            j += 1
        for _, i in idx[k:j + 1]:
            out[i] = 100 * ((k + j) / 2) / (n - 1) if n > 1 else 50.0
        k = j + 1
    return out


def score(rows, weights=(40, 30, 30)):
    """Return new rows with demand, spend, gap (0 to 100) and opportunity (weighted average)."""
    col = lambda k: [r.get(k) for r in rows]
    demand_parts = [pct_rank(col(k)) for k in ["density", "age16_34", "students", "kids_hh", "one_person_hh", "flats", "renters"]]
    income = pct_rank([r["net_income"] * r["takeaway_multiplier"] if r.get("net_income") else None for r in rows])
    deprived = pct_rank(col("income_deprivation"))
    comp = pct_rank(col("outlets_per_1000"))
    wd, ws, wg = weights
    out = []
    for i, r in enumerate(rows):
        d = sum(p[i] for p in demand_parts) / len(demand_parts)
        s = 0.6 * income[i] + 0.4 * (100 - deprived[i]) if income[i] is not None and deprived[i] is not None else None
        g = 100 - comp[i]
        parts = [(d, wd), (s, ws), (g, wg)]
        parts = [(x, w) for x, w in parts if x is not None]  # a missing layer drops out instead of scoring zero
        opp = sum(x * w for x, w in parts) / sum(w for _, w in parts) if sum(w for _, w in parts) else None
        out.append({**r, "demand": round(d, 1), "spend": None if s is None else round(s, 1), "gap": round(g, 1),
                    "opportunity": None if opp is None else round(opp, 1)})
    return out


def _self_check():
    assert pct_rank([1, 2, 3]) == [0, 50, 100] and pct_rank([5, 5, None]) == [50, 50, None]
    base = {"density": 1, "age16_34": .2, "students": .1, "kids_hh": .3, "one_person_hh": .3, "flats": .4, "renters": .5,
            "net_income": 40000, "takeaway_multiplier": 1.0, "income_deprivation": .1, "outlets_per_1000": 0}
    rows = [{**base, "outlets_per_1000": c, "density": d} for c, d in [(0, 1), (1, 2), (2, 3)]]
    s = score(rows)
    assert all(0 <= r[k] <= 100 for r in s for k in ["demand", "spend", "gap", "opportunity"])
    assert s[0]["gap"] > s[1]["gap"] > s[2]["gap"], "more outlets must mean a smaller gap"
    assert score(rows, (100, 0, 0))[2]["opportunity"] == s[2]["demand"]
    assert "opportunity" not in rows[0], "score must not mutate its input"
    assert outlet_weight("5") == 1 and outlet_weight("1") == 0.5 and outlet_weight(None) == 1


# ---------- build ----------
def main(refresh=False):
    _self_check()
    from shapely.geometry import shape, Point
    from shapely.ops import transform, unary_union
    from shapely import STRtree
    from pyproj import Transformer

    pol = load_policy_and_spend()
    fs = pol.get("family_spending") or {}
    if fs.get("london_takeaway_gbp_week") and fs.get("east_takeaway_gbp_week") and fs.get("uk_takeaway_gbp_week"):
        mult = {"London": fs["london_takeaway_gbp_week"] / fs["uk_takeaway_gbp_week"],
                "East of England": fs["east_takeaway_gbp_week"] / fs["uk_takeaway_gbp_week"]}
    else:
        mult = {"London": 1.0, "East of England": 1.0}  # dashboard says the multiplier could not be fetched

    bpath = RAW / "msoa_boundaries.geojson"
    def fetch_bounds():
        w = " OR ".join(f"MSOA21NM LIKE '{a} %'" for a in AREAS)
        q = urllib.parse.urlencode({"where": w, "outFields": "MSOA21CD,MSOA21NM,RUC21NM", "outSR": 4326,
                                    "maxAllowableOffset": 0.00015, "geometryPrecision": 5, "f": "geojson"})
        return get(URLS["boundaries"] + "/query?" + q)
    feats = json.loads(cached(bpath, fetch_bounds))["features"]
    census, income, lookup = load_census(), load_income(), load_lookup()
    imd = load_imd(lookup)

    polys = [shape(f["geometry"]) for f in feats]
    s, w = min(p.bounds[1] for p in polys), min(p.bounds[0] for p in polys)
    n, e = max(p.bounds[3] for p in polys), max(p.bounds[2] for p in polys)
    fsa = json.loads(cached(RAW / "competitors" / "fsa_pizza.json", fetch_fsa, refresh))
    osm = json.loads(cached(RAW / "competitors" / "osm_pizza.json", lambda: fetch_osm((s, w, n, e)), refresh))["elements"]
    comps = merge_competitors(fsa, osm)
    tree = STRtree(polys)
    inside = []
    for c in comps:
        hit = [i for i in tree.query(Point(c["lon"], c["lat"]), predicate="intersects")]
        if hit:
            c["msoa"] = feats[hit[0]]["properties"]["MSOA21CD"]
            inside.append(c)

    to_bng = Transformer.from_crs(4326, 27700, always_xy=True).transform
    schools = load_schools(pol)
    zones = unary_union([Point(sc["e"], sc["n"]).buffer(sc["radius"]) for sc in schools]) if schools else None

    rows = []
    for f, poly in zip(feats, polys):
        code, name = f["properties"]["MSOA21CD"], f["properties"]["MSOA21NM"]
        area = name.rsplit(" ", 1)[0]
        _, region, ring = AREAS[area]
        c = census.get(code, {})
        mine = [x for x in inside if x["msoa"] == code]
        weighted = sum(outlet_weight(x["rating"]) for x in mine)
        bng = transform(to_bng, poly)
        rows.append({"code": code, "name": name, "area": area, "region": region, "ring": ring, "urban_rural": f["properties"].get("RUC21NM"),
                     **{k: c.get(k) for k in ["population", "households", "density", "age16_34", "students", "kids_hh", "one_person_hh", "flats", "renters"]},
                     "net_income": income.get(code), "income_deprivation": imd.get(code), "takeaway_multiplier": round(mult[region], 3),
                     "outlets": len(mine), "chains": sum(1 for x in mine if x["chain"]), "independents": sum(1 for x in mine if not x["chain"]),
                     "outlets_per_1000": round(1000 * weighted / c["population"], 3) if c.get("population") else None,
                     "school_zone_share": round(bng.intersection(zones).area / bng.area, 3) if zones is not None else None,
                     "zone_rule": next((r.get("applies") for r in pol.get("takeaway_school_rules", []) if r["council"] == area), None)})
    scored = score(rows)
    for r in scored:
        for k in ["age16_34", "students", "kids_hh", "one_person_hh", "flats", "renters", "income_deprivation"]:
            r[k] = None if r[k] is None else round(r[k], 4)
    missing = [r["code"] for r in scored if r["opportunity"] is None or r["net_income"] is None or r["income_deprivation"] is None]

    sources = {
        "census": {"short": "Census 2021", "label": "ONS Census 2021 (TS001, TS003, TS006, TS007A, TS044, TS054, TS066) via Nomis bulk download", "url": "https://www.nomisweb.co.uk/sources/census_2021_bulk", "date": fetched_on(RAW / "census" / "census2021-ts001-msoa.csv")},
        "boundaries": {"short": "ONS boundaries", "label": "ONS Open Geography Portal, MSOA 2021 full extent boundaries (simplified on download)", "url": URLS["boundaries"], "date": fetched_on(bpath)},
        "income": {"short": "ONS income FYE 2023", "label": "ONS Income estimates for small areas, FYE 2023, net annual household income (published 10 Dec 2025)", "url": URLS["income"], "date": fetched_on(RAW / "income" / "saie_fye2023.xlsx")},
        "imd": {"short": "IoD 2025", "label": "English Indices of Deprivation 2025, File 7, income domain rate (LSOA, population weighted to MSOA)", "url": URLS["imd"], "date": fetched_on(RAW / "imd" / "File_7_IoD2025.csv")},
        "lookup": {"short": "ONS lookup", "label": "ONS OA to LSOA to MSOA lookup, December 2021", "url": URLS["lookup"], "date": fetched_on(RAW / "lsoa_msoa_lookup.json")},
        "family_spending": {"short": "ONS Family Spending", "label": (fs.get("release") or "ONS Family Spending") + (f", {fs.get('table')}" if fs.get("table") else ""), "url": fs.get("url"), "date": pol.get("retrieved"),
                            "note": None if fs.get("uk_takeaway_gbp_week") else "Could not be fetched. Regional multiplier set to 1.0 for both regions."},
        "fsa": {"short": "FSA ratings", "label": "Food Standards Agency food hygiene rating API (names containing pizza, domino or papa john; takeaways, restaurants and mobile caterers)", "url": "https://api.ratings.food.gov.uk/", "date": fetched_on(RAW / "competitors" / "fsa_pizza.json")},
        "osm": {"short": "OpenStreetMap", "label": "OpenStreetMap via Overpass (amenity fast_food or restaurant with cuisine pizza, or pizza chain name)", "url": URLS["overpass"], "date": fetched_on(RAW / "competitors" / "osm_pizza.json")},
        "schools": {"short": "DfE GIAS", "label": "DfE Get Information about Schools, open secondary and all-through schools", "url": "https://get-information-schools.service.gov.uk/Downloads", "date": fetched_on(RAW / "schools" / "edubase_latest.csv")},
        "nppf": {"short": "NPPF HC5", "label": (pol.get("national_policy") or {}).get("name", "National Planning Policy Framework"), "url": (pol.get("national_policy") or {}).get("url"), "date": pol.get("retrieved")},
        "policy": {"short": "Council plans", "label": "Council local plans and the London Plan on hot food takeaways near schools (one link per council in the panel)", "url": None, "date": pol.get("retrieved"),
                   "note": None if pol.get("takeaway_school_rules") else "Council policy research not available. Zones not drawn."},
    }
    geo = {"type": "FeatureCollection",
           "meta": {"built": datetime.date.today().isoformat(), "sources": sources, "family_spending": fs, "multipliers": mult,
                    "missing": missing, "rules": pol.get("takeaway_school_rules", []), "national_policy": pol.get("national_policy"), "areas": {a: v[2] for a, v in AREAS.items()}},
           "features": [{"type": "Feature", "geometry": f["geometry"], "properties": r} for f, r in zip(feats, scored)]}
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.geojson").write_text(json.dumps(geo, separators=(",", ":")))
    (DOCS / "competitors.json").write_text(json.dumps([{k: c[k] for k in ["name", "lat", "lon", "address", "postcode", "rating", "rating_date", "fsa_type", "fhrsid", "osm", "source", "chain", "msoa"]} for c in inside], separators=(",", ":")))
    (DOCS / "schools.json").write_text(json.dumps([{k: sc[k] for k in ["urn", "name", "la", "postcode", "lat", "lon", "rule", "radius"]} for sc in schools], separators=(",", ":")))

    # version every asset link so GitHub Pages never serves a stale file
    idx = DOCS / "index.html"
    if idx.exists():
        html = idx.read_text()
        for asset in ["app.js", "style.css", "data.geojson", "competitors.json", "schools.json"]:
            if (DOCS / asset).exists():
                v = hashlib.md5((DOCS / asset).read_bytes()).hexdigest()[:8]
                html = re.sub(rf"{re.escape(asset)}\?v=[0-9a-f]+", f"{asset}?v={v}", html)
        idx.write_text(html)
    print(f"{len(scored)} MSOAs, {len(inside)} pizza outlets ({sum(1 for c in inside if c['chain'])} chain), "
          f"{len(schools)} secondary schools, {len(missing)} MSOAs with a missing layer")


if __name__ == "__main__":
    main(refresh="--refresh-competitors" in sys.argv)
