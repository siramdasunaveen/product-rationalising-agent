"""Step 15 domain: connector configuration rules, Salesforce queries, record-to-template rows."""

from app.domain import connectors as cd


def body(**over):
    base = {"key": "sf-prod", "name": "Salesforce production", "system": "SALESFORCE",
            "instanceUrl": "https://acme.my.salesforce.com"}
    base.update(over)
    return base


def test_validate_config():
    assert cd.validate_config(body()) == []
    bad = cd.validate_config(body(key="SF Prod", instanceUrl="http://evil.example", system="DYNAMICS",
                                  apiVersion="61", queries={"CPQ": {"object": "1bad"}}))
    assert sorted(e.field for e in bad) == ["apiVersion", "instanceUrl", "key", "queries.CPQ", "system"]
    assert cd.validate_config(body(queries="x"))[0].field == "queries"


def test_default_queries_cover_catalog_price_book_and_cpq_only_with_metadata_fields():
    assert set(cd.SALESFORCE_QUERIES) == {"CRM_CATALOG", "PRICE_BOOK", "CPQ"}
    crm = cd.query_for("CRM_CATALOG", None)
    assert cd.soql(crm) == ("SELECT Description, Family, IsActive, Name, ProductCode, StockKeepingUnit FROM Product2 "
                            "WHERE IsActive = true OR IsActive = false")
    assert cd.query_for("INVENTORY", None) is None
    custom = cd.query_for("INVENTORY", {"INVENTORY": {"object": "Asset_Aggregate__c", "columns": {"Service Spec ID": "Spec__c"}}})
    assert cd.soql(custom) == "SELECT Spec__c FROM Asset_Aggregate__c"
    pb = cd.query_for("PRICE_BOOK", None)
    assert "Pricebook2.Name" in cd.soql(pb) and "Product2.ProductCode" in cd.soql(pb)


def test_to_row_follows_relationship_paths_maps_and_constants():
    crm = cd.query_for("CRM_CATALOG", None)
    rec = {"ProductCode": "PRD-V1", "Name": "Mobile Data 15GB V1", "Family": "Postpaid", "IsActive": True,
           "StockKeepingUnit": "OFR-1001", "Description": "15GB", "attributes": {"type": "Product2"}}
    assert cd.to_row(rec, crm) == {"Product ID": "PRD-V1", "Product Name": "Mobile Data 15GB V1",
                                   "Product Family": "Postpaid", "Status": "Active", "Offer ID": "OFR-1001",
                                   "Description": "15GB"}
    pb = cd.query_for("PRICE_BOOK", None)
    row = cd.to_row({"Pricebook2": {"Name": "PB-UK-2026"}, "Product2": {"ProductCode": "PRD-V1"},
                     "CurrencyIsoCode": "GBP", "UnitPrice": 15.0}, pb)
    assert row == {"Price Book ID": "PB-UK-2026", "Product ID": "PRD-V1", "Currency": "GBP", "List Price": 15.0}
    cpq = cd.query_for("CPQ", None)
    row = cd.to_row({"SBQQ__ConfiguredSKU__r": {"ProductCode": "PRD-V1"}, "SBQQ__OptionalSKU__r": None,
                     "SBQQ__Required__c": False, "SBQQ__MaxQuantity__c": 1}, cpq)
    assert row["Rule Type"] == "Configuration" and row["Required/Optional"] == "Optional"
    assert row["Attribute/Option ID"] is None and row["Cardinality"] == 1


def test_redact_never_leaks_credentials():
    assert cd.redact({"instanceUrl": "x", "clientId": "a", "clientSecret": "b"}) == {"instanceUrl": "x"}
