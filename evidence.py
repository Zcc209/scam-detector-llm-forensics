"""Report records whose IDs can be cited by the final assessment."""


def domain_records(initial=None, browser=None):
    records = []
    if initial:
        records.append({"id": "domain:initial", "analysis": initial})
    if browser:
        records.extend({"id": f"domain:navigation:{index}", "analysis": item}
                       for index, item in enumerate(browser.get("navigation_checks") or []))
        if browser.get("final_domain_analysis"):
            records.append({"id": "domain:final", "analysis": browser["final_domain_analysis"]})
    return records
