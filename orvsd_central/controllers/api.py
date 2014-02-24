from flask import Blueprint, abort, jsonify, request

from orvsd_central.database import db_session
from orvsd_central.models import Course, CourseDetail, School, Site, SiteDetail
from orvsd_central.util import (district_details, get_obj_by_category,
                                string_to_type)

mod = Blueprint('api', __name__)


@mod.route("/<category>/object/add", methods=["POST"])
def add_object(category):
    obj = get_obj_by_category(category)
    if obj:
        inputs = {}
        # Here we update our dict with new values
        # A one liner is too messy :(
        for column in obj.__table__.columns:
            if column.name is not 'id':
                inputs.update({column.name: string_to_type(
                               request.form.get(column.name))})

        new_obj = obj(**inputs)
        db_session.add(new_obj)
        db_session.commit()
        return jsonify({'id': new_obj.id,
                        'message': "Object added successfully!"})

    abort(404)


# Get all task IDs
# TODO: Needs testing
@mod.route('/celery/id/all')
def get_all_ids():
    # TODO: "result" is another column, but SQLAlchemy
    # complains of some encoding error.
    statuses = db_session.query("id", "task_id", "status",
                                "date_done", "traceback")\
                         .from_statement("SELECT * FROM celery_taskmeta")\
                         .all()

    return jsonify(status=statuses)


@mod.route("/1/site/<site_id>/courses")
def get_courses_by_site(site_id):
    #SiteDetails hold the course information we are looking for
    site_details = SiteDetail.query.filter_by(site_id=site_id) \
                                   .order_by(SiteDetail
                                             .timemodified
                                             .desc()) \
                                   .first()

    if site_details and site_details.courses:
        return jsonify(content=json.loads(site_details.courses))
    elif not site_details:
        return jsonify({'error:': 'Site not found.'})
    else:
        return jsonify({'error:': 'No courses found.'})


@mod.route("/courses/filter", methods=["POST"])
def get_course_list():
    dir = request.form.get('filter')

    if dir == "None":
        courses = CourseDetail.query.all()
    else:
        courses = db_session.query(CourseDetail).join(Course) \
                    .filter(Course.source == dir).all()

    # This means the folder selected was not the source folder or None.
    if not courses:
        courses = db_session.query(CourseDetail).filter(CourseDetail.filename
                                                        .like("%"+dir+"%"))\
                                                .all()

    courses = sorted(courses, key=lambda x: x.course.name)

    serialized_courses = [{'id': course.course_id,
                           'name': course.course.name}
                          for course in courses]
    return jsonify(courses=serialized_courses)


@mod.route("/<category>/keys")
def get_keys(category):
    obj = get_obj_by_category(category)
    if obj:
        cols = dict((column.name, '') for column in
                    obj.__table__.columns)
        return jsonify(cols)


@mod.route("/1/sites/<baseurl>/moodle")
def get_moodle_sites(baseurl):
    school_id = Site.query.filter_by(baseurl=baseurl).first().school_id
    moodle_sites = Site.query.filter_by(school_id=school_id).all()
    data = [{'id': site.id, 'name': site.name} for site in moodle_sites]
    return jsonify(content=data)


@mod.route("/<category>/<id>", methods=["GET"])
def get_object(category, id):
    obj = get_obj_by_category(category)
    if obj:
        modified_obj = obj.query.filter_by(id=id).first()
        if modified_obj:
            return jsonify(modified_obj.serialize())

    abort(404)


@mod.route('/report/get_schools', methods=['POST'])
def get_schools():
    # From the POST, we need the district id, or distid
    dist_id = request.form.get('distid')

    # Given the distid, we get all the schools
    if dist_id:
        schools = School.query.filter_by(district_id=dist_id) \
                              .order_by("name").all()
    else:
        schools = School.query.order_by("name").all()

    # the dict to be jsonify'd
    school_list = {}

    for school in schools:
        sitedata = []
        sites = Site.query.filter(Site.school_id == school.id).all()
        admincount = 0
        teachercount = 0
        usercount = 0
        for site in sites:
            admin = None
            sd = SiteDetail.query.filter(SiteDetail.site_id == site.id)\
                                 .order_by(SiteDetail.timemodified.desc())\
                                 .first()
            if sd:
                admin = sd.adminemail
                admincount = admincount + sd.adminusers
                teachercount = teachercount + sd.teachers
                usercount = usercount + sd.totalusers
            sitedata.append({'name': site.name,
                             'baseurl': site.baseurl,
                             'sitetype': site.sitetype,
                             'admin': admin})
        usercount = usercount - admincount - teachercount
        school_list[school.shortname] = {'name': school.name,
                                         'id': school.id,
                                         'admincount': admincount,
                                         'teachercount': teachercount,
                                         'usercount': usercount,
                                         'sitedata': sitedata}

    # Returned the jsonify'd data of counts and schools for jvascript to parse
    return jsonify(schools=school_list, counts=district_details(schools))


@mod.route("/1/sites/<baseurl>")
def get_site_by_url(baseurl):
    site = Site.query.filter_by(baseurl=baseurl).first()
    if site:
        site_details = SiteDetail.query.filter_by(site_id=site.id) \
                                       .order_by(SiteDetail
                                                 .timemodified
                                                 .desc()) \
                                       .first()

        site_info = dict(site.serialize().items() +
                         site_details.serialize().items())

        return jsonify(content=site_info)
    return jsonify(content={'error': 'Site not found'})


@mod.route('/celery/status/<celery_id>')
def get_task_status(celery_id):
    status = db_session.query("status")\
                       .from_statement("SELECT status FROM celery_taskmeta"
                                       " WHERE id=:celery_id")\
                       .params(celery_id=celery_id).first()
    return jsonify(status=status)


@mod.route('/get_site_by/<int:site_id>', methods=['GET'])
def site_by_id(site_id):
    name = Site.query.filter_by(id=site_id).first().name
    return jsonify(name=name)