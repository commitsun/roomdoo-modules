# PMS BookAI Integration

WhatsApp notifications via BookAI API for Property Management System.

## Features

- BookAI WhatsApp channel integration for PMS notifications
- Configurable API credentials and endpoints
- Automatic parameter extraction from reservations, folios, payments, and invoices
- Support for multiple languages in WhatsApp templates
- Complete error handling and logging

## Configuration

1. Go to **Settings > Technical > PMS > BookAI Configuration**
2. Set your BookAI API endpoint (default: https://bookai.predev.roomdoo.com)
3. Enter your API Bearer token
4. The Instance ID is automatically populated from your Odoo instance URL

## Usage

### Create WhatsApp Notification Templates

1. Navigate to **PMS > Configuration > Notification Templates**
2. Create or edit a notification template
3. Fill in the BookAI fields:
   - **BookAI Template Code**: Template code in BookAI system (e.g.,
     `payment_reminder_urgent_v1`)
   - **BookAI Template Language**: Select the language
   - **BookAI Template Parameters**: JSON list of expected parameters

### Create Notification Rules

1. Go to **PMS > Configuration > Notification Rules**
2. Create a new rule
3. Select **BookAI WhatsApp** as the channel
4. Link to your WhatsApp-enabled template
5. Configure trigger conditions

### Requirements

Partners must have:

- Phone or mobile number in international format (starting with `+`)
- Country set (for ISO country code)

Properties should have:

- External code configured for BookAI identification

## Technical

The module extends:

- `pms.notification.template` - Adds WhatsApp template configuration
- `pms.notification.log` - Adds WhatsApp sending capability
- `pms.property` - Adds external hotel code
- `res.partner` - Adds WhatsApp phone/country helpers
