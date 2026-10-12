#  🏬 User Warehouse Restriction

| | |
|---|---|
| **Author** | ERP Addons |
| **Website** | https://www.erp-addons.com |
| **Version** | 19.0.1.0.0 |
| **License** | OPL-1 |
| **Odoo** | 19.0 |

---

# 📖 Overview

**User Warehouse Restriction** helps you control which warehouses and stock locations each user can access.

You can assign one or more warehouses to a user and restrict access to all other warehouses. This improves security, prevents users from working with the wrong warehouse, and ensures they only see inventory related to their assigned locations.

The module integrates seamlessly with Odoo Inventory and is easy to configure.

---

# 🚀 Key Features

## 👤 Restrict Warehouse Access

Allow users to access only selected warehouses.

**📍 Menu Navigation**

`Settings → Users & Companies → Users`

**Features**

- Assign one or more warehouses to a user.
- Restrict access to other warehouses.
- Improve warehouse security.
- Simple user configuration.

---

## 📦 Restrict Stock Locations

Control which stock locations users can work with.

**Features**

- Limit access to specific locations.
- Prevent unauthorized stock operations.
- Keep inventory organized.
- Reduce user errors.

---

## 🔒 Improve Inventory Security

Ensure users only work with the warehouses assigned to them.

**Benefits**

- Better access control.
- Protect inventory data.
- Reduce operational mistakes.
- Improve warehouse management.

---

## ⚙️ Easy Configuration

Configure warehouse restrictions directly from the user form.

No complex setup or customization is required.

---

# ⚙️ Installation

1. Copy the **user_warehouse_restriction** module into your Odoo addons directory.
2. Update the Apps List.

**📍 Menu Navigation**

`Apps → Update Apps List`

3. Search for **User Warehouse Restriction**.
4. Click **Install**.

---

# 📖 Usage

## 👤 Configure User Warehouses

**📍 Menu Navigation**

`Settings → Users & Companies → Users`

### Steps

1. Open a user.
2. Select the warehouses the user can access.
3. Save the user.

---

## 🏬 Manage Warehouses

**📍 Menu Navigation**

`Inventory → Configuration → Warehouses`

### Steps

1. Open a warehouse.
2. Configure the required settings.
3. Save the changes.

Only authorized users will be able to access the assigned warehouses.

---

## 📦 Perform Stock Operations

Users can create and manage inventory operations only for the warehouses and stock locations assigned to them.

---

# 🏗️ Architecture

```text
user.warehouse.restriction
│
├── User Warehouse Configuration
├── Warehouse Access Control
├── Stock Location Restriction
└── Inventory Security
```

---

# 📌 Changelog

## 19.0.1.0.0 — Initial Release

- Restrict warehouse access for users.
- Restrict stock location access.
- Configure allowed warehouses from the user form.
- Improve inventory security.
- Seamless integration with Odoo Inventory.
- Fully compatible with Odoo 19.

---

# 💬 Support

For questions, feature requests, or technical support, please visit:

🌐 **https://www.erp-addons.com**