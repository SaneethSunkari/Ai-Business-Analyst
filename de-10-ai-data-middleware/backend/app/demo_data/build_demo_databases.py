from __future__ import annotations

import itertools
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


SEED = 20260510
ROOT = Path(__file__).resolve().parent


FIRST_NAMES = [
    "Ava", "Mia", "Liam", "Noah", "Emma", "Olivia", "Ethan", "Lucas", "Sophia", "Amelia",
    "Isabella", "James", "Harper", "Elijah", "Charlotte", "Benjamin", "Evelyn", "Henry",
    "Abigail", "Jack", "Emily", "Daniel", "Ella", "Sofia", "Grace", "Samuel", "Chloe",
    "Leo", "Layla", "Owen", "Mason", "Zoey", "Nora", "Aria", "Isaac", "Avery",
]

LAST_NAMES = [
    "Carter", "Mitchell", "Patel", "Nguyen", "Sullivan", "Brooks", "Reed", "Foster",
    "Barnes", "Coleman", "Diaz", "Morales", "Hughes", "Ross", "Sanders", "Howard",
    "Bennett", "Ward", "Murphy", "Powell", "Kim", "Turner", "James", "Watson",
    "Richardson", "Cooper", "Bailey", "Cook", "Morgan", "Bell", "Perry", "Long",
]

CITY_REGION_STATE = [
    ("Boston", "Northeast", "MA"),
    ("New York", "Northeast", "NY"),
    ("Providence", "Northeast", "RI"),
    ("Chicago", "Midwest", "IL"),
    ("Columbus", "Midwest", "OH"),
    ("Austin", "South", "TX"),
    ("Atlanta", "South", "GA"),
    ("Miami", "South", "FL"),
    ("Seattle", "West", "WA"),
    ("San Francisco", "West", "CA"),
    ("Denver", "West", "CO"),
    ("Phoenix", "West", "AZ"),
]

INDUSTRY_SEGMENTS = [
    "Enterprise",
    "Mid-Market",
    "SMB",
    "Direct",
    "Partner",
    "Digital",
]

PRODUCT_CATEGORIES = [
    "Electronics",
    "Home",
    "Beauty",
    "Grocery",
    "Outdoor",
    "Office",
    "Apparel",
    "Fitness",
]

EVENT_NAMES = [
    "login",
    "dashboard_view",
    "api_call",
    "report_export",
    "subscription_update",
    "invoice_view",
    "search",
    "collaboration_comment",
]


@dataclass
class DepartmentSeed:
    name: str
    location: str
    cost_center_prefix: str


FINANCE_DEPARTMENTS = [
    DepartmentSeed("Finance Operations", "Boston", "FIN"),
    DepartmentSeed("Corporate Accounting", "New York", "ACC"),
    DepartmentSeed("Sales Operations", "Chicago", "SAL"),
    DepartmentSeed("People Operations", "Austin", "HR"),
    DepartmentSeed("Customer Success", "Atlanta", "CS"),
    DepartmentSeed("Marketing", "San Francisco", "MKT"),
    DepartmentSeed("Engineering", "Seattle", "ENG"),
    DepartmentSeed("Procurement", "Denver", "PRC"),
    DepartmentSeed("Legal", "Boston", "LEG"),
    DepartmentSeed("Facilities", "Miami", "FAC"),
    DepartmentSeed("Business Intelligence", "New York", "BI"),
    DepartmentSeed("Revenue Operations", "Chicago", "REV"),
]

HR_DEPARTMENTS = [
    DepartmentSeed("Engineering", "Seattle", "ENG"),
    DepartmentSeed("Product", "San Francisco", "PRD"),
    DepartmentSeed("People", "Boston", "PEO"),
    DepartmentSeed("Finance", "New York", "FIN"),
    DepartmentSeed("Sales", "Chicago", "SAL"),
    DepartmentSeed("Marketing", "Austin", "MKT"),
    DepartmentSeed("Customer Success", "Atlanta", "CS"),
    DepartmentSeed("Operations", "Denver", "OPS"),
]


def reset_db(filename: str) -> sqlite3.Connection:
    path = ROOT / filename
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def random_date(rng: random.Random, start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=rng.randint(0, max(delta, 0)))


def random_datetime(rng: random.Random, start: datetime, end: datetime) -> datetime:
    delta_seconds = int((end - start).total_seconds())
    return start + timedelta(seconds=rng.randint(0, max(delta_seconds, 0)))


def amount(rng: random.Random, low: float, high: float, digits: int = 2) -> float:
    return round(rng.uniform(low, high), digits)


def fake_email(first: str, last: str, idx: int, domain: str = "example.com") -> str:
    return f"{first.lower()}.{last.lower()}{idx}@{domain}"


def build_finance_db() -> None:
    rng = random.Random(SEED + 1)
    conn = reset_db("finance.db")
    conn.executescript(
        """
        CREATE TABLE departments (
            department_id INTEGER PRIMARY KEY,
            department_name TEXT NOT NULL,
            division_name TEXT NOT NULL,
            department_head TEXT NOT NULL,
            location TEXT NOT NULL,
            created_on DATE NOT NULL
        );

        CREATE TABLE cost_centers (
            cost_center_id INTEGER PRIMARY KEY,
            cost_center_code TEXT NOT NULL UNIQUE,
            cost_center_name TEXT NOT NULL,
            department_id INTEGER NOT NULL,
            budget_owner TEXT NOT NULL,
            annual_budget NUMERIC NOT NULL,
            active_flag TEXT NOT NULL,
            created_on DATE NOT NULL,
            FOREIGN KEY (department_id) REFERENCES departments(department_id)
        );

        CREATE TABLE accounts (
            account_id INTEGER PRIMARY KEY,
            account_number TEXT NOT NULL UNIQUE,
            account_name TEXT NOT NULL,
            account_type TEXT NOT NULL,
            department_id INTEGER NOT NULL,
            cost_center_id INTEGER NOT NULL,
            currency_code TEXT NOT NULL,
            current_balance NUMERIC NOT NULL,
            account_status TEXT NOT NULL,
            opened_on DATE NOT NULL,
            FOREIGN KEY (department_id) REFERENCES departments(department_id),
            FOREIGN KEY (cost_center_id) REFERENCES cost_centers(cost_center_id)
        );

        CREATE TABLE transactions (
            transaction_id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            department_id INTEGER NOT NULL,
            cost_center_id INTEGER NOT NULL,
            transaction_date DATE NOT NULL,
            transaction_type TEXT NOT NULL,
            transaction_amount NUMERIC NOT NULL,
            vendor_name TEXT,
            region TEXT NOT NULL,
            fiscal_period TEXT NOT NULL,
            description TEXT,
            FOREIGN KEY (account_id) REFERENCES accounts(account_id),
            FOREIGN KEY (department_id) REFERENCES departments(department_id),
            FOREIGN KEY (cost_center_id) REFERENCES cost_centers(cost_center_id)
        );
        """
    )

    department_rows = []
    for idx in range(1, 13):
        seed = FINANCE_DEPARTMENTS[idx - 1]
        leader = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        division = rng.choice(["Corporate", "Go-To-Market", "Shared Services", "Technology"])
        department_rows.append(
            (
                idx,
                seed.name,
                division,
                leader,
                seed.location,
                random_date(rng, date(2018, 1, 1), date(2022, 12, 31)).isoformat(),
            )
        )
    conn.executemany(
        "INSERT INTO departments VALUES (?, ?, ?, ?, ?, ?)",
        department_rows,
    )

    cost_center_rows = []
    for idx in range(1, 121):
        dept = department_rows[(idx - 1) % len(department_rows)]
        code = f"{FINANCE_DEPARTMENTS[(idx - 1) % len(FINANCE_DEPARTMENTS)].cost_center_prefix}-{1000 + idx}"
        cost_center_rows.append(
            (
                idx,
                code,
                f"{dept[1].split()[0]} Cost Center {idx:03d}",
                dept[0],
                f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                amount(rng, 250_000, 4_500_000),
                rng.choice(["active", "active", "active", "inactive"]),
                random_date(rng, date(2019, 1, 1), date(2024, 1, 1)).isoformat(),
            )
        )
    conn.executemany(
        "INSERT INTO cost_centers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        cost_center_rows,
    )

    account_types = ["Expense", "Revenue", "Asset", "Liability"]
    account_rows = []
    for idx in range(1, 521):
        dept = rng.choice(department_rows)
        cost_center = rng.choice([row for row in cost_center_rows if row[3] == dept[0]])
        account_type = rng.choices(account_types, weights=[0.45, 0.2, 0.25, 0.1], k=1)[0]
        balance_range = {
            "Expense": (4_000, 180_000),
            "Revenue": (15_000, 250_000),
            "Asset": (30_000, 900_000),
            "Liability": (10_000, 350_000),
        }[account_type]
        account_rows.append(
            (
                idx,
                f"{400000 + idx}",
                f"{account_type} Account {idx:03d}",
                account_type,
                dept[0],
                cost_center[0],
                rng.choice(["USD", "USD", "USD", "EUR"]),
                amount(rng, *balance_range),
                rng.choice(["open", "open", "open", "review"]),
                random_date(rng, date(2016, 1, 1), date(2024, 3, 1)).isoformat(),
            )
        )
    conn.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        account_rows,
    )

    transaction_rows = []
    now = date.today()
    for idx in range(1, 2601):
        account = rng.choice(account_rows)
        txn_date = random_date(rng, now - timedelta(days=540), now)
        transaction_type = rng.choices(
            ["expense", "revenue", "transfer", "adjustment"],
            weights=[0.48, 0.24, 0.18, 0.1],
            k=1,
        )[0]
        region = rng.choice([city[1] for city in CITY_REGION_STATE])
        vendor = rng.choice(
            [
                "Delta Office Supply",
                "Nexa Cloud Services",
                "BridgePoint Consulting",
                "Urban Facilities Group",
                "Northwind Travel",
                "BluePeak Media",
                "Vertex Analytics",
            ]
        )
        transaction_rows.append(
            (
                idx,
                account[0],
                account[4],
                account[5],
                txn_date.isoformat(),
                transaction_type,
                amount(rng, 850, 95_000),
                vendor,
                region,
                f"{txn_date.year}-{txn_date.month:02d}",
                f"{transaction_type.title()} posted for {vendor}",
            )
        )
    conn.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        transaction_rows,
    )

    conn.commit()
    conn.close()


def build_retail_db() -> None:
    rng = random.Random(SEED + 2)
    conn = reset_db("retail.db")
    conn.executescript(
        """
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            customer_name TEXT NOT NULL,
            email_address TEXT NOT NULL UNIQUE,
            region TEXT NOT NULL,
            city TEXT NOT NULL,
            state_code TEXT NOT NULL,
            loyalty_tier TEXT NOT NULL,
            signup_date DATE NOT NULL
        );

        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            sku TEXT NOT NULL UNIQUE,
            product_name TEXT NOT NULL,
            category_name TEXT NOT NULL,
            brand_name TEXT NOT NULL,
            unit_price NUMERIC NOT NULL,
            unit_cost NUMERIC NOT NULL,
            active_flag TEXT NOT NULL,
            launch_date DATE NOT NULL
        );

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_date DATE NOT NULL,
            order_status TEXT NOT NULL,
            region TEXT NOT NULL,
            sales_channel TEXT NOT NULL,
            shipping_amount NUMERIC NOT NULL,
            order_total NUMERIC NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price NUMERIC NOT NULL,
            discount_amount NUMERIC NOT NULL,
            line_revenue NUMERIC NOT NULL,
            return_quantity INTEGER NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(order_id),
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        );

        CREATE TABLE inventory (
            inventory_id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL,
            warehouse_name TEXT NOT NULL,
            stock_on_hand INTEGER NOT NULL,
            reorder_level INTEGER NOT NULL,
            inventory_value NUMERIC NOT NULL,
            snapshot_date DATE NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        );
        """
    )

    customer_rows = []
    for idx in range(1, 1001):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        city, region, state = rng.choice(CITY_REGION_STATE)
        customer_rows.append(
            (
                idx,
                f"{first} {last}",
                fake_email(first, last, idx, "shopco.example"),
                region,
                city,
                state,
                rng.choice(["Bronze", "Silver", "Gold", "Platinum"]),
                random_date(rng, date(2020, 1, 1), date(2026, 3, 31)).isoformat(),
            )
        )
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?)", customer_rows)

    product_rows = []
    for idx in range(1, 241):
        category = PRODUCT_CATEGORIES[(idx - 1) % len(PRODUCT_CATEGORIES)]
        brand = rng.choice(["Aster", "Northwind", "Summit", "Everlane", "Vertex", "Greenline"])
        unit_price = amount(rng, 14, 480)
        product_rows.append(
            (
                idx,
                f"SKU-{idx:05d}",
                f"{brand} {category} Item {idx:03d}",
                category,
                brand,
                unit_price,
                round(unit_price * rng.uniform(0.32, 0.68), 2),
                rng.choice(["active", "active", "active", "seasonal"]),
                random_date(rng, date(2019, 1, 1), date(2025, 1, 31)).isoformat(),
            )
        )
    conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", product_rows)

    order_rows = []
    for idx in range(1, 1201):
        customer = rng.choice(customer_rows)
        order_date = random_date(rng, date.today() - timedelta(days=730), date.today())
        order_rows.append(
            (
                idx,
                customer[0],
                order_date.isoformat(),
                rng.choice(["completed", "completed", "completed", "returned", "cancelled"]),
                customer[3],
                rng.choice(["Online", "Retail", "Wholesale", "Marketplace"]),
                amount(rng, 4, 34),
                0.0,
            )
        )
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)", order_rows)

    order_item_rows = []
    order_totals: dict[int, float] = {row[0]: 0.0 for row in order_rows}
    for idx in range(1, 3201):
        order = rng.choice(order_rows)
        product = rng.choice(product_rows)
        quantity = rng.randint(1, 6)
        unit_price = product[5]
        discount = amount(rng, 0, unit_price * quantity * 0.18)
        line_revenue = round(unit_price * quantity - discount, 2)
        return_quantity = rng.choices([0, 1, 2], weights=[0.88, 0.1, 0.02], k=1)[0]
        order_item_rows.append(
            (
                idx,
                order[0],
                product[0],
                quantity,
                unit_price,
                discount,
                line_revenue,
                min(return_quantity, quantity),
            )
        )
        order_totals[order[0]] += line_revenue
    conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)", order_item_rows)
    conn.executemany(
        "UPDATE orders SET order_total = ? WHERE order_id = ?",
        [(round(total + next(row[6] for row in order_rows if row[0] == order_id), 2), order_id) for order_id, total in order_totals.items()],
    )

    inventory_rows = []
    snapshot = date.today().isoformat()
    for idx, product in enumerate(product_rows, start=1):
        stock = rng.randint(20, 950)
        inventory_rows.append(
            (
                idx,
                product[0],
                rng.choice(["East Fulfillment", "Central Distribution", "West Coast Hub"]),
                stock,
                rng.randint(15, 120),
                round(stock * product[6], 2),
                snapshot,
            )
        )
    conn.executemany("INSERT INTO inventory VALUES (?, ?, ?, ?, ?, ?, ?)", inventory_rows)

    conn.commit()
    conn.close()


def build_hr_db() -> None:
    rng = random.Random(SEED + 3)
    conn = reset_db("hr.db")
    conn.executescript(
        """
        CREATE TABLE departments (
            department_id INTEGER PRIMARY KEY,
            department_name TEXT NOT NULL,
            division_name TEXT NOT NULL,
            location_name TEXT NOT NULL,
            department_head TEXT NOT NULL
        );

        CREATE TABLE employees (
            employee_id INTEGER PRIMARY KEY,
            employee_number TEXT NOT NULL UNIQUE,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            department_id INTEGER NOT NULL,
            job_title TEXT NOT NULL,
            location_name TEXT NOT NULL,
            employment_status TEXT NOT NULL,
            hire_date DATE NOT NULL,
            manager_id INTEGER,
            FOREIGN KEY (department_id) REFERENCES departments(department_id),
            FOREIGN KEY (manager_id) REFERENCES employees(employee_id)
        );

        CREATE TABLE salaries (
            salary_id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            annual_salary NUMERIC NOT NULL,
            bonus_target NUMERIC NOT NULL,
            currency_code TEXT NOT NULL,
            effective_date DATE NOT NULL,
            FOREIGN KEY (employee_id) REFERENCES employees(employee_id)
        );

        CREATE TABLE performance_reviews (
            review_id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL,
            review_period DATE NOT NULL,
            review_score NUMERIC NOT NULL,
            manager_rating NUMERIC NOT NULL,
            promotion_recommendation TEXT NOT NULL,
            summary_notes TEXT,
            FOREIGN KEY (employee_id) REFERENCES employees(employee_id)
        );
        """
    )

    department_rows = []
    for idx, seed in enumerate(HR_DEPARTMENTS, start=1):
        department_rows.append(
            (
                idx,
                seed.name,
                rng.choice(["Corporate", "Go-To-Market", "Product & Technology", "Shared Services"]),
                seed.location,
                f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            )
        )
    conn.executemany("INSERT INTO departments VALUES (?, ?, ?, ?, ?)", department_rows)

    titles = [
        "Analyst", "Senior Analyst", "Manager", "Senior Manager", "Specialist", "Director",
        "Engineer", "Senior Engineer", "Coordinator", "Partner",
    ]
    employee_rows = []
    for idx in range(1, 321):
        dept = rng.choice(department_rows)
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        employee_rows.append(
            (
                idx,
                f"EMP-{idx:05d}",
                first,
                last,
                dept[0],
                f"{rng.choice(['Lead', 'Principal', 'Associate', ''])} {rng.choice(titles)}".strip(),
                dept[3],
                rng.choice(["active", "active", "active", "leave", "inactive"]),
                random_date(rng, date(2014, 1, 1), date(2026, 1, 1)).isoformat(),
                rng.randint(1, max(1, idx - 1)) if idx > 8 else None,
            )
        )
    conn.executemany("INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", employee_rows)

    salary_rows = []
    for idx, employee in enumerate(employee_rows, start=1):
        dept_name = next(row[1] for row in department_rows if row[0] == employee[4])
        salary_base = {
            "Engineering": (110_000, 225_000),
            "Product": (105_000, 205_000),
            "People": (75_000, 150_000),
            "Finance": (85_000, 180_000),
            "Sales": (70_000, 210_000),
            "Marketing": (80_000, 165_000),
            "Customer Success": (72_000, 155_000),
            "Operations": (78_000, 170_000),
        }[dept_name]
        annual_salary = amount(rng, *salary_base)
        salary_rows.append(
            (
                idx,
                employee[0],
                annual_salary,
                amount(rng, annual_salary * 0.05, annual_salary * 0.2),
                "USD",
                random_date(rng, date(2023, 1, 1), date(2026, 1, 1)).isoformat(),
            )
        )
    conn.executemany("INSERT INTO salaries VALUES (?, ?, ?, ?, ?, ?)", salary_rows)

    review_rows = []
    review_id = 1
    for employee in employee_rows:
        periods = [date(2024, 6, 30), date(2025, 6, 30)]
        for period in periods:
            if rng.random() < 0.78:
                score = round(rng.uniform(2.8, 5.0), 2)
                review_rows.append(
                    (
                        review_id,
                        employee[0],
                        period.isoformat(),
                        score,
                        round(min(5.0, max(1.0, score + rng.uniform(-0.3, 0.3))), 2),
                        rng.choice(["yes", "yes", "no", "watchlist"]),
                        rng.choice(
                            [
                                "Strong collaborator with consistent execution.",
                                "Delivers well against goals and communicates clearly.",
                                "Shows leadership potential in cross-functional work.",
                                "Needs coaching on prioritization and delegation.",
                            ]
                        ),
                    )
                )
                review_id += 1
    conn.executemany("INSERT INTO performance_reviews VALUES (?, ?, ?, ?, ?, ?, ?)", review_rows)

    conn.commit()
    conn.close()


def build_saas_db() -> None:
    rng = random.Random(SEED + 4)
    conn = reset_db("saas.db")
    conn.executescript(
        """
        CREATE TABLE plans (
            plan_id INTEGER PRIMARY KEY,
            plan_name TEXT NOT NULL,
            plan_tier TEXT NOT NULL,
            billing_interval TEXT NOT NULL,
            monthly_price NUMERIC NOT NULL,
            annual_price NUMERIC NOT NULL,
            active_flag TEXT NOT NULL
        );

        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            email_address TEXT NOT NULL UNIQUE,
            company_name TEXT NOT NULL,
            region TEXT NOT NULL,
            acquisition_channel TEXT NOT NULL,
            created_at DATE NOT NULL,
            user_status TEXT NOT NULL
        );

        CREATE TABLE subscriptions (
            subscription_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            subscription_status TEXT NOT NULL,
            started_at DATE NOT NULL,
            trial_end_at DATE,
            canceled_at DATE,
            monthly_recurring_revenue NUMERIC NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
        );

        CREATE TABLE invoices (
            invoice_id INTEGER PRIMARY KEY,
            subscription_id INTEGER NOT NULL,
            invoice_date DATE NOT NULL,
            invoice_amount NUMERIC NOT NULL,
            payment_status TEXT NOT NULL,
            billing_period TEXT NOT NULL,
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(subscription_id)
        );

        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            event_name TEXT NOT NULL,
            event_date DATE NOT NULL,
            event_value NUMERIC NOT NULL,
            platform_name TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        """
    )

    plan_rows = [
        (1, "Starter", "SMB", "monthly", 49, 490, "active"),
        (2, "Growth", "SMB", "monthly", 99, 990, "active"),
        (3, "Scale", "Mid-Market", "monthly", 249, 2490, "active"),
        (4, "Enterprise", "Enterprise", "monthly", 799, 7990, "active"),
        (5, "Starter Annual", "SMB", "annual", 41, 490, "active"),
        (6, "Scale Annual", "Mid-Market", "annual", 208, 2490, "active"),
    ]
    conn.executemany("INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?)", plan_rows)

    user_rows = []
    for idx in range(1, 821):
        region = rng.choice([city[1] for city in CITY_REGION_STATE])
        company = f"{rng.choice(['Northwind', 'Vertex', 'Aster', 'BluePeak', 'Summit', 'Beacon'])} {rng.choice(['Analytics', 'Health', 'Retail', 'Capital', 'Logistics', 'Software'])}"
        user_rows.append(
            (
                idx,
                fake_email(rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES), idx, "saasco.example"),
                company,
                region,
                rng.choice(["Organic", "Partner", "Outbound", "Paid Search", "Referral"]),
                random_date(rng, date(2022, 1, 1), date(2026, 4, 1)).isoformat(),
                rng.choice(["active", "active", "active", "paused", "canceled"]),
            )
        )
    conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", user_rows)

    subscription_rows = []
    for idx, user in enumerate(user_rows, start=1):
        plan = rng.choice(plan_rows)
        started = random_date(rng, date(2022, 1, 1), date(2026, 4, 1))
        status = rng.choices(["active", "active", "active", "trialing", "canceled"], weights=[0.52, 0.18, 0.12, 0.08, 0.1], k=1)[0]
        canceled_at = None
        if status == "canceled":
            canceled_at = random_date(rng, started + timedelta(days=45), date.today()).isoformat()
        trial_end_at = (started + timedelta(days=14)).isoformat()
        monthly_recurring_revenue = plan[4]
        subscription_rows.append(
            (
                idx,
                user[0],
                plan[0],
                status,
                started.isoformat(),
                trial_end_at,
                canceled_at,
                monthly_recurring_revenue,
            )
        )
    conn.executemany("INSERT INTO subscriptions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", subscription_rows)

    invoice_rows = []
    invoice_id = 1
    for subscription in subscription_rows:
        started = datetime.strptime(subscription[4], "%Y-%m-%d").date()
        end_date = datetime.strptime(subscription[6], "%Y-%m-%d").date() if subscription[6] else date.today()
        cursor = started
        while cursor <= end_date and invoice_id <= 2200:
            invoice_rows.append(
                (
                    invoice_id,
                    subscription[0],
                    cursor.isoformat(),
                    float(subscription[7]),
                    rng.choice(["paid", "paid", "paid", "pending"]),
                    f"{cursor.year}-{cursor.month:02d}",
                )
            )
            invoice_id += 1
            cursor = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
    conn.executemany("INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?)", invoice_rows)

    event_rows = []
    event_id = 1
    event_start = datetime.now() - timedelta(days=180)
    for _ in range(4200):
        user = rng.choice(user_rows)
        event_time = random_datetime(rng, event_start, datetime.now())
        event_rows.append(
            (
                event_id,
                user[0],
                rng.choice(EVENT_NAMES),
                event_time.date().isoformat(),
                amount(rng, 1, 100, 0),
                rng.choice(["web", "mobile", "api"]),
            )
        )
        event_id += 1
    conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", event_rows)

    conn.commit()
    conn.close()


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    build_finance_db()
    build_retail_db()
    build_hr_db()
    build_saas_db()
    print("Created finance.db, retail.db, hr.db, and saas.db in", ROOT)


if __name__ == "__main__":
    main()
