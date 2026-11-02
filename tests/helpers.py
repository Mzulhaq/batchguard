import csv
import io

HEADER = ["order_id", "ordered_at", "customer_id", "sku", "quantity", "unit_price", "currency"]


def sale(order_id="o-1", **overrides):
    row = dict(
        zip(
            HEADER,
            [order_id, "2026-11-01T10:00:00Z", "customer-1", "sku-1", "2", "12.50", "USD"],
            strict=True,
        )
    )
    row.update(overrides)
    return row


def sales_csv(*rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()
