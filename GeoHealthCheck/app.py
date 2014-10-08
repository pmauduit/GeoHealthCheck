# =================================================================
#
# Authors: Tom Kralidis <tomkralidis@gmail.com>
#
# Copyright (c) 2014 Tom Kralidis
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use,
# copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following
# conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.
#
# =================================================================

from flask import (abort, flash, Flask, g, jsonify, redirect,
                   render_template, request, url_for)

from flask.ext.login import (LoginManager, login_user, logout_user,
                             current_user, login_required)

from __init__ import __version__
from healthcheck import run_test_resource
from init import DB
from enums import RESOURCE_TYPES
from models import Resource, Run, User
import views

APP = Flask(__name__)
APP.config.from_pyfile('config.py')
APP.config.from_pyfile('../instance/config.py')
APP.secret_key = APP.config['SECRET_KEY']

LOGIN_MANAGER = LoginManager()
LOGIN_MANAGER.init_app(APP)


@LOGIN_MANAGER.user_loader
def load_user(identifier):
    return User.query.get(int(identifier))


@LOGIN_MANAGER.unauthorized_handler
def unauthorized_callback():
    if request.query_string:
        url = '%s?%s' % (request.path, request.query_string)
    else:
        url = request.path
    return redirect(url_for('login', next=url))


@APP.before_request
def before_request():
    g.user = current_user


@APP.template_filter('cssize_reliability')
def cssize_reliability(value):
    """returns CSS class snippet based on score"""

    number = int(value)

    if 0 <= number <= 49:
        score = 'danger'
    elif 50 <= number <= 79:
        score = 'warning'
    elif 80 <= number <= 100:
        score = 'success'
    else:  # should never really get here
        score = 'info'
    return score


@APP.template_filter('round2')
def round2(value):
    """rounds a number to 2 decimal places except for values of 0 or 100"""

    if value in [0.0, 100.0]:
        return int(value)
    return round(value, 2)


@APP.context_processor
def context_processors():
    return {
        'app_version': __version__,
        'resource_types': RESOURCE_TYPES,
    }


@APP.route('/')
def home():
    """homepage"""

    resource_type = None

    if request.args.get('resource_type') in RESOURCE_TYPES.keys():
        resource_type = request.args['resource_type']

    response = views.list_resources(resource_type)
    return render_template('home.html', response=response)


@APP.route('/export')
def export():
    """export resource list as JSON"""

    resource_type = None

    if request.args.get('resource_type') in RESOURCE_TYPES.keys():
        resource_type = request.args['resource_type']

    response = views.list_resources(resource_type)
    json_dict = {'resources': []}
    for r in response['resources']:
        json_dict['resources'].append({
            'resource_type': r.resource_type,
            'title': r.title,
            'url': r.url
        })
    return jsonify(json_dict)


@APP.route('/settings')
def settings():
    """settings"""
    pass


@APP.route('/resource/<identifier>')
def get_resource_by_id(identifier):
    """show resource"""

    response = views.get_resource_by_id(identifier)
    return render_template('resource.html', resource=response)


@APP.route('/register', methods=['GET', 'POST'])
def register():
    if not APP.config['GHC_SELF_REGISTER']:
        flash('This site is not configured for self-registration.  '
              'Please contact %s ' % APP.config['GHC_ADMIN_EMAIL'], 'warning')
        return redirect(url_for('login'))
    if request.method == 'GET':
        return render_template('register.html')
    user = User(request.form['username'],
                request.form['password'], request.form['email'])
    DB.session.add(user)
    try:
        DB.session.commit()
    except Exception, err:
        DB.session.rollback()
        bad_column = err.message.split()[2]
        bad_value = request.form[bad_column]
        flash('%s %s already registered' % (bad_column, bad_value), 'danger')
        return redirect(url_for('register'))
    return redirect(url_for('login'))


@APP.route('/add', methods=['GET', 'POST'])
@login_required
def add():
    if not g.user.is_authenticated():
        return render_template('add.html')
    if request.method == 'GET':
        return render_template('add.html')

    resource_type = request.form['resource_type']
    url = request.form['url'].strip()
    resource = Resource.query.filter_by(resource_type=resource_type,
                                        url=url).first()
    if resource is not None:
        flash('service already registered (%s, %s)' % (resource_type, url),
              'danger')
        if 'resource_type' in request.args:
            rtype = request.args.get('resource_type')
            return redirect(url_for('add',
                                    resource_type=rtype))
        return redirect(url_for('add'))

    [title, success, response_time, message, start_time] = run_test_resource(
        resource_type, url)

    resource_to_add = Resource(current_user, resource_type, title, url)
    run_to_add = Run(resource_to_add, success, response_time, message,
                     start_time)

    DB.session.add(resource_to_add)
    DB.session.add(run_to_add)
    try:
        DB.session.commit()
        flash('service registered (%s, %s)' % (resource_type, url), 'success')
    except Exception, err:
        DB.session.rollback()
        flash(str(err), 'danger')
    return redirect(url_for('home'))


@APP.route('/resource/<int:resource_identifier>/test')
@login_required
def test(resource_identifier):
    resource = Resource.query.filter_by(identifier=resource_identifier).first()
    if resource is None:
        flash('resource not found', 'danger')
        return redirect(request.referrer)

    [title, success, response_time, message, start_time] = run_test_resource(
        resource.resource_type, resource.url)
    run_to_add = Run(resource, success, response_time, message, start_time)

    if message not in ['OK', None, 'None']:
        flash('ERROR: %s' % message, 'danger')
    else:
        flash('Resource tested successfully', 'success')

    DB.session.add(run_to_add)

    try:
        DB.session.commit()
    except Exception, err:
        DB.session.rollback()
        flash(str(err), 'danger')
    return redirect(url_for('get_resource_by_id',
                    identifier=resource_identifier))


@APP.route('/resource/<int:resource_identifier>/delete')
@login_required
def delete(resource_identifier):
    pass


@APP.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    username = request.form['username']
    password = request.form['password']
    registered_user = User.query.filter_by(username=username,
                                           password=password).first()
    if registered_user is None:
        flash('invalid username and / or password', 'danger')
        return redirect(url_for('login'))
    login_user(registered_user)

    if 'next' in request.args:
        return redirect(request.args.get('next'))
    return redirect(url_for('home'))


@APP.route('/logout')
def logout():
    logout_user()
    flash('logged out', 'success')
    if request.referrer:
        return redirect(request.referrer)
    else:
        return redirect(url_for('home'))


if __name__ == '__main__':  # run locally, for fun
    import sys
    HOST = '0.0.0.0'
    PORT = 8000
    if len(sys.argv) > 1:
        HOST, PORT = sys.argv[1].split(':')
    APP.run(host=HOST, port=int(PORT), use_reloader=True, debug=True)
