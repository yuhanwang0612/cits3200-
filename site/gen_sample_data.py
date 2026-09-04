"""Generate SAMPLE data for the site-design shell. Not real data."""
import json, random, os, unicodedata, re

random.seed(3200)

UNIS = [
    ("ANU",  "The Australian National University"),
    ("UNSW", "The University of New South Wales"),
    ("UQ",   "The University of Queensland"),
    ("USYD", "The University of Sydney"),
    ("UWA",  "The University of Western Australia"),
    ("MON",  "Monash University"),
    ("ADL",  "The University of Adelaide"),
    ("UOM",  "The University of Melbourne"),
]
FOR = ["Accounting", "Finance"]
LEVELS = [("A","Associate Lecturer"),("B","Lecturer"),("C","Senior Lecturer"),
          ("D","Associate Professor"),("E","Professor")]

FIRST = ["Amara","Nikhil","Elena","Tomas","Priya","Marcus","Wei","Sofia","Daniel","Ingrid",
         "Hassan","Clara","Yusuf","Mei","Oliver","Ana","Rahul","Freya","Lucas","Noor",
         "Kwame","Isabel","Jonas","Leila","Andres","Hana","Fionn","Talia","Rowan","Sena",
         "Bruno","Anika","Emil","Zara","Kiran","Maja","Otto","Nadia","Caleb","Ilse"]
LAST  = ["Vance","Okafor","Lindqvist","Moreau","Bhatt","Kowalski","Nakamura","Ferreira",
         "Aldridge","Petrov","Haddad","Ruiz","Ozturk","Lindgren","Boateng","Kaur","Novak",
         "Cheng","Delacroix","Mbeki","Solberg","Rinaldi","Farkas","Ivanova","Dumont",
         "Salazar","Whitmore","Adeyemi","Kalinen","Berger"]

JOURNALS = [
 ("Journal of Accounting Research","0021-8456","A*",6.1),
 ("The Accounting Review","0001-4826","A*",5.4),
 ("Journal of Finance","0022-1082","A*",7.6),
 ("Review of Financial Studies","0893-9454","A*",6.8),
 ("Journal of Financial Economics","0304-405X","A*",8.9),
 ("Contemporary Accounting Research","0823-9150","A*",3.2),
 ("Accounting and Finance","0810-5391","A",2.7),
 ("Journal of Banking and Finance","0378-4266","A",3.6),
 ("European Accounting Review","0963-8180","A",3.1),
 ("Review of Corporate Finance Studies","2046-9128","A",None),
 ("Journal of Corporate Finance","0929-1199","A",4.2),
 ("Auditing: A Journal of Practice and Theory","0278-0380","A",2.4),
 ("Accounting Horizons","0888-7993","A",None),
 ("Financial Markets, Institutions and Instruments","0963-8008","B",None),
 ("Australian Journal of Management","0312-8962","B",2.1),
 ("Journal of Business Finance and Accounting","0306-686X","B",2.6),
 ("Pacific-Basin Finance Journal","0927-538X","B",4.0),
 ("Managerial Auditing Journal","0268-6902","C",None),
 ("Journal of Applied Accounting Research","0967-5426","C",None),
 ("International Review of Economics and Finance","1059-0560",None,None),
 ("Emerging Markets Review","1566-0141",None,None),
]

TOPIC_A = ["Corporate","Institutional","Cross-border","Retail","Algorithmic","Sustainable",
           "Post-crisis","Dynamic","Empirical","Comparative","Asymmetric","Behavioural"]
TOPIC_B = ["disclosure quality","audit fees","capital structure","earnings management",
           "liquidity provision","credit risk","dividend policy","tax avoidance",
           "market microstructure","ESG reporting","board diversity","short selling",
           "analyst forecasts","executive compensation","bank lending","IPO underpricing"]
TOPIC_C = ["in emerging markets","and firm value","under regulatory change",
           "evidence from Australia","during monetary tightening","and investor attention",
           "in family firms","after IFRS adoption","and the cost of equity",
           "in the banking sector"]

def slug(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii","ignore").decode()
    return re.sub(r"[^a-z0-9]+","-", s.lower()).strip("-")

researchers, universities, pubs_by_r = [], [], {}
used = set()

for code, uname in UNIS:
    n = random.randint(9, 16)
    for _ in range(n):
        while True:
            nm = f"{random.choice(FIRST)} {random.choice(LAST)}"
            if nm not in used:
                used.add(nm); break
        lv, lvname = random.choice(LEVELS)
        rid = f"{code.lower()}-{slug(nm)}"
        weight = {"A":0.4,"B":0.7,"C":1.0,"D":1.5,"E":2.2}[lv]
        npub = max(3, int(random.gauss(16*weight, 7)))
        pubs, counts = [], {"A*":0,"A":0,"B":0,"C":0,"none":0}
        for _ in range(npub):
            jn, issn, abdc, jif = random.choice(JOURNALS)
            title = f"{random.choice(TOPIC_A)} {random.choice(TOPIC_B)} {random.choice(TOPIC_C)}"
            year = random.randint(2015, 2026)
            has_doi = random.random() < 0.78
            doi = f"10.{random.randint(1000,1111)}/{slug(jn)[:6]}.{year}.{random.randint(1000,9999)}" if has_doi else None
            rank = abdc if abdc else "none"
            counts[rank] += 1
            pubs.append({
                "title": title,
                "journal_name": jn,
                "issn": issn,
                "year": year,
                "quality_rank": abdc,
                "impact_factor": jif,
                "scimago_quartile": random.choice(["Q1","Q1","Q2","Q3",None]),
                "cited_by_count": random.randint(0, 340) if has_doi else None,
                "doi": doi,
                "article_url": f"https://doi.org/{doi}" if doi else None,
                "publication_type": "journal_article",
            })
        pubs.sort(key=lambda p: (-p["year"], p["title"]))
        ranked = counts["A*"]+counts["A"]+counts["B"]+counts["C"]
        researchers.append({
            "id": rid, "name": nm, "university": uname, "university_code": code,
            "field_of_research": random.choice(FOR), "academic_level": lvname,
            "level_code": lv,
            "publication_count": len(pubs), "abdc_ranked_count": ranked,
            "count_a_star": counts["A*"], "count_a": counts["A"],
            "count_b": counts["B"], "count_c": counts["C"], "count_unranked": counts["none"],
        })
        pubs_by_r[rid] = {"researcher": researchers[-1], "publications": pubs}

for code, uname in UNIS:
    rs = [r for r in researchers if r["university_code"] == code]
    universities.append({
        "code": code, "name": uname,
        "researcher_count": len(rs),
        "publication_count": sum(r["publication_count"] for r in rs),
        "abdc_ranked_count": sum(r["abdc_ranked_count"] for r in rs),
    })

os.makedirs("/home/claude/site-design/data/publications", exist_ok=True)
meta = {"generated": "2026-09-03", "is_sample_data": True,
        "note": "SAMPLE DATA generated for the site design shell. Replace with real exports."}
json.dump({"meta": meta, "universities": universities}, open("/home/claude/site-design/data/universities.json","w"), indent=1)
json.dump({"meta": meta, "researchers": researchers}, open("/home/claude/site-design/data/researchers.json","w"), indent=1)
for rid, payload in pubs_by_r.items():
    payload["meta"] = meta
    json.dump(payload, open(f"/home/claude/site-design/data/publications/{rid}.json","w"), indent=1)

print("researchers:", len(researchers), "publications:", sum(r["publication_count"] for r in researchers))
