import json
from application import app
from application.documents import get_document, get_image
import requests
import logging
import re
from datetime import datetime
import logging


def insert_new_application(cursor, data):
    app_data = data['application_data']
    delivery_method = data['delivery_method'] if 'delivery_method' in data else None

    cursor.execute("INSERT INTO pending_application (application_data, date_received, "
                   "application_type, status, work_type, delivery_method) " +
                   "VALUES (%(json)s, %(date)s, %(type)s, %(status)s, %(work_type)s, %(delivery)s) "
                   "RETURNING id", {"json": json.dumps(app_data), "date": data['date_received'],
                                    "type": data['application_type'],
                                    "status": "new", "work_type": data['work_type'],
                                    "delivery": delivery_method})
    item_id = cursor.fetchone()[0]
    return item_id


def get_application_list(cursor, list_type):
    bank_regn_type = ''
    if list_type == 'pab':
        bank_regn_type = 'PA(B)'
    elif list_type == 'wob':
        bank_regn_type = 'WO(B)'

    if list_type == 'all':
        cursor.execute(" SELECT id, date_received, application_data, application_type, status, work_type, "
                       " assigned_to, delivery_method "
                       " FROM pending_application "
                       " WHERE lock_ind IS NULL "
                       " order by date_received desc")
    elif bank_regn_type != '':
        cursor.execute("SELECT id, date_received, application_data, application_type, status, work_type, "
                       " assigned_to, delivery_method "
                       " FROM pending_application "
                       " WHERE application_type=%(bank_regn_type)s AND lock_ind IS NULL "
                       " order by date_received desc",
                       {"bank_regn_type": bank_regn_type})
    else:
        cursor.execute("SELECT id, date_received, application_data, application_type, status, work_type, "
                       " assigned_to, delivery_method "
                       " FROM pending_application "
                       " WHERE work_type=%(list_type)s AND lock_ind IS NULL "
                       " order by date_received", {"list_type": list_type})
    rows = cursor.fetchall()
    applications = []

    for row in rows:
        result = {
            "appn_id": row['id'],
            "application_data": row['application_data'],
            "date_received": str(row['date_received']),
            "application_type": row['application_type'],
            "status": row['status'],
            "work_type": row['work_type'],
            "assigned_to": row['assigned_to'],
            "delivery_method": row['delivery_method']
        }
        applications.append(result)
    return applications


def get_application_by_id(cursor, appn_id):
    cursor.execute("SELECT date_received, application_data, application_type, status, work_type, assigned_to "
                   "FROM pending_application "
                   "WHERE id=%(id)s", {"id": appn_id})
    rows = cursor.fetchall()

    if len(rows) == 0:
        return None
    row = rows[0]
    return {
        "appn_id": appn_id,
        "application_data": row['application_data'],
        "date_received": str(row['date_received']),
        "application_type": row['application_type'],
        "status": row['status'],
        "work_type": row['work_type'],
        "assigned_to": row['assigned_to'],
    }


def set_lock_ind(cursor, appn_id):
    cursor.execute("UPDATE pending_application SET lock_ind = 'Y' "
                   "WHERE id=%(id)s and lock_ind IS NULL ", {"id": appn_id})

    if cursor.rowcount == 0:
        return None
    else:
        return "success"


def clear_lock_ind(cursor, appn_id):
    cursor.execute("UPDATE pending_application SET lock_ind = NULL "
                   "WHERE id=%(id)s", {"id": appn_id})


def update_application_details(cursor, appn_id, data):
    cursor.execute("UPDATE pending_application SET application_data=%(data)s, status=%(status)s, "
                   "assigned_to=%(assign)s WHERE id=%(id)s", {
                       "data": data['application_data'],
                       "status": data['status'],
                       "assign": data['assigned_to'],
                       "id": appn_id
                   })


def delete_application(cursor, appn_id):
    cursor.execute('DELETE from pending_application where id=%(id)s', {'id': appn_id})
    return cursor.rowcount


def amend_application(cursor, appn_id, data):
    reg_no = data['regn_no']
    date = data['registration']['date']
    url = app.config['LAND_CHARGES_URI'] + '/registrations/' + date + '/' + reg_no
    headers = {'Content-Type': 'application/json'}
    response = requests.put(url, data=json.dumps(data), headers=headers)
    if response.status_code != 200:
        return response

    # Archive amendment docs under new ID
    regns = response.json()
    date_string = datetime.now().strftime("%Y_%m_%d")
    for reg_no in regns['new_registrations']:
        url = app.config['DOCUMENT_API_URI'] + '/archive/' + date_string + '/' + str(reg_no)
        body = {'document_id': data['document_id']}
        doc_response = requests.post(url, data=json.dumps(body), headers=headers)
        if doc_response.status_code != 200:
            return doc_response

    # Delete work-item
    delete_application(cursor, appn_id)

    # return regn nos
    return regns


def create_lc_registration(data):
    coc_lut = {
        'C(I)': 'C1',
        'C(II)': 'C2',
        'C(III)': 'C3',
        'C(IV)': 'C4',
        'D(I)': 'D1',
        'D(II)': 'D2',
        'D(III)': 'D3',
    }

    c = data['lc_register_details']['class']
    if c in coc_lut:
        c = coc_lut[c]
    else:
        c = re.sub("[\(\)]", "", c)

    registration = {
        "parties": [],
        "class_of_charge": c,
        "applicant": {
            "name": data['customer_name'],
            "address": data['customer_address'],
            "key_number": data["key_number"],
            "reference": data['application_ref']
        }
    }

    party = {
        "type": "Estate Owner",
        "names": []
    }

    name_data = data['lc_register_details']["estate_owner"]
    name = {
        "type": name_data['estate_owner_ind']
    }

    if name['type'] == 'Private Individual':
        name['private'] = {
            'forenames': name_data['private']['forenames'],
            'surname': name_data['private']['surname']
        }
    elif name['type'] == "County Council" or name['type'] == "Parish Council" or name['type'] == "Other Council":
        name['local'] ={
            'name': name_data['local']['name'],
            'area': name_data['local']['area']
        }
    elif name['type'] == "Development Corporation" or name['type'] == "Other":
        name['other'] = name_data['other']
    elif name['type'] == "Limited Company":
        name['company'] = name_data['company']
    elif name['type'] == "Complex Name":
        name['complex'] = {
            'name': name_data['complex']['name'],
            'number': name_data['complex']['number']
        }
    else:
        raise RuntimeError("Unexpected name type: {}".format(name['type']))

    party['names'].append(name)
    party['occupation'] = data['lc_register_details']['occupation']
    registration['parties'].append(party)

    if 'additional_info' in data['lc_register_details']:
        registration['additional_information'] = data['lc_register_details']['additional_info']

    registration['particulars'] = {
        "counties": data['lc_register_details']['county'],
        "district": data['lc_register_details']['district'],
        "description": data['lc_register_details']['short_description']
    }

    if data['lc_register_details']['priority_notice'] != '':
        registration['particulars']['priority_notice'] = data['lc_register_details']['priority_notice']

    return registration


#{"private": {"forenames": ["Bob"], "surname": "Howard"}, "complex": {"number": 0, "name": ""},
# "estate_owner_ind": "Private Individual", "company": "", "local": {"area": "", "name": ""}, "other": ""},

#"application_ref": "reference 11", "document_id": 66, "class_of_charge": "New Registration",
# "customer_name": "Mr Conveyancer", "application_data": {"document_id": 66}, "appn_id": "2271", "form": "K1",
# "residence_withheld": false, "status": "new", "date_received": "2015-11-05 14:01:57",
# "customer_address": "2 New Street", "date_of_birth": "1980-01-01", "date": "2016-02-04",
# "lc_register_details": {"county": ["Devon"], "class": "C(I)",
# "estate_owner":
# "additional_info": "dsfsd df sd", "estate_owner_ind": "Private Individual", "occupation": "Civl Servant",
# "district": "Nine", "short_description": "Wibble"}, "key_number": "244095", "assigned_to": null,
# "application_type": "K1", "work_type": "lc_regn"}

def complete_application(cursor, appn_id, data):
    # Submit registration
    url = app.config['LAND_CHARGES_URI'] + '/registrations'
    headers = {'Content-Type': 'application/json'}

    print(data)

    response = requests.post(url, data=json.dumps(create_lc_registration(data)), headers=headers)
    if response.status_code != 200:
        logging.error(response.text)
        raise RuntimeError("Unexpected response from /registrations: {}".format(response.status_code))

    regns = response.json()

    # Insert print job
    insert_result_row(cursor, regns['request_id'], 'registration')
    # TODO error handling on inserting print job row


    # Archive document
    document_id = data['application_data']['document_id']
    pages = get_document(cursor, document_id)

    logging.warn("TEMPORARY LEGDB SUPRESSION")
    if False:
        for regn in regns['new_registrations']:
            number = regn['number']
            date = regn['date']
            for page in pages:
                image = get_image(cursor, document_id, page)
                url = "{}/images/{}/{}/{}".format(app.config['LEGACY_ADAPTER_URI'],
                                                  date,
                                                  number,
                                                  'A4')
                headers = {'Content-Type': image['mimetype']}
                doc_response = requests.put(url, data=image['bytes'], headers=headers)
                if doc_response.status_code != 200:
                    # TODO: error!
                    pass

    # Delete work-item
    delete_application(cursor, appn_id)

    # return regn nos
    return regns


def bulk_insert_applications(cursor, data):  # pragma: no cover
    items = []
    for item in data:
        app_data = {
            "document_id": item['document_id']
        }
        cursor.execute("INSERT INTO pending_application (application_data, date_received, "
                       "application_type, status, work_type, delivery_method) " +
                       "VALUES (%(json)s, %(date)s, %(type)s, %(status)s, %(work_type)s, %(delivery)s) "
                       "RETURNING id", {"json": json.dumps(app_data), "date": item['date'],
                                        "type": item['application_type'],
                                        "status": "new", "work_type": item['work_type'],
                                        "delivery": item['delivery_method']})
        items.append(cursor.fetchone()[0])
    return items


# insert a print job row on the result table
def insert_result_row(cursor, request_id, result_type):
    try:
        cursor.execute("INSERT into results(request_id, res_type, print_status) values(%(request_id)s, %(res_type)s, "
                       " %(print_status)s) ",
                       {
                           'request_id': request_id,
                           'res_type': result_type,
                           'print_status': "",
                       })
    except:
        raise
    return "success"
