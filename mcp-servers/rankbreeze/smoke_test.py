"""Quick end-to-end check of every tool against the live cookie."""
import json
import rankbreeze_client as rb

print("== health_check ==")
print(json.dumps(rb.health_check(), indent=2))
props = rb.list_properties()
print("\n== list_properties ==")
print(json.dumps(props, indent=2))
lid = props["listings"][0]["id"]
print(f"\n== get_rankings({lid}) ==")
print(json.dumps(rb.get_rankings(lid), indent=2))
print(f"\n== get_metrics({lid}) ==")
print(json.dumps(rb.get_metrics(lid), indent=2))
print(f"\n== get_calendar_rankings({lid}) ==")
print(json.dumps(rb.get_calendar_rankings(lid), indent=2))
print(f"\n== get_competitor_rates({lid}) ==")
print(json.dumps(rb.get_competitor_rates(lid), indent=2))
print("\nALL TOOLS OK")
