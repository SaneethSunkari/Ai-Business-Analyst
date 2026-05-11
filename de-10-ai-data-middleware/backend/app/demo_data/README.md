# Cross-Domain Demo Databases

This folder contains four SQLite databases for cross-domain validation testing.

## `finance.db`
Tables:
- `accounts`
- `transactions`
- `cost_centers`
- `departments`

Sample questions:
- `Total expenses by department this year`
- `Top 10 accounts by balance`
- `Monthly transaction volume last 6 months`

## `retail.db`
Tables:
- `products`
- `orders`
- `order_items`
- `customers`
- `inventory`

Sample questions:
- `Top 20 products by revenue`
- `Return rate by category`
- `Customer count by region`

## `hr.db`
Tables:
- `employees`
- `departments`
- `salaries`
- `performance_reviews`

Sample questions:
- `Average salary by department`
- `Headcount by location`
- `Top performers by review score`

## `saas.db`
Tables:
- `users`
- `subscriptions`
- `invoices`
- `events`
- `plans`

Sample questions:
- `Monthly recurring revenue by plan`
- `Active users last 30 days`
- `Churn count by month`

## Regenerate

To rebuild the demo databases:

```bash
python backend/app/demo_data/build_demo_databases.py
```
