import json
import os

from flask import (Blueprint, abort, current_app, flash, g, render_template,
                   request)
from flask.ext.login import current_user, login_required
from sqlalchemy import and_

from orvsd_central.forms import InstallCourse
from orvsd_central.models import (CourseDetail, District, School, Site,
                                  SiteCourse, SiteDetail)
from orvsd_central.util import (create_course_from_moodle_backup,
                                get_course_folders, get_path_and_source,
                                get_obj_by_category, get_obj_identifier,
                                install_course_to_site, requires_role,
                                update_courselist_task)

mod = Blueprint('category', __name__)


"""
All
"""


@mod.route("/<category>/update")
@requires_role('helpdesk')
@login_required
def update(category):
    """
    Returns a rendered template that shows all objects in a given 'category'.
    """
    obj = get_obj_by_category(category)
    identifier = get_obj_identifier(category)
    if obj:
        if 'details' in category:
            category = category.split("details")[0] + " Details"
        category = category[0].upper() + category[1:]

        objects = obj.query.order_by(identifier).all()
        objects = objects if objects else []
        return render_template(
            "update.html", objects=objects,
            identifier=identifier, category=category,
            user=current_user
        )
    else:
        abort(404)


"""
Course
"""


@mod.route('/courses/install', methods=['GET', 'POST'])
@requires_role('helpdesk')
@login_required
def install_course():
    """
    Displays a form for the admin user to pick courses to install on a site

    Returns:
        Rendered template
    """

    if request.method == 'GET':
        form = InstallCourse()

        # Query all moodle 2.x courses
        courses = g.db_session.query(CourseDetail).filter(
            CourseDetail.moodle_version
            .like('2%')
            ).all()

        # Query all moodle sites
        sites = g.db_session.query(Site).filter(
            Site.sitetype == 'moodle')

        moodle_2_sites = []

        # For all sites query the SiteDetail to see if it's a moodle 2.x site
        for site in sites:
            details = g.db_session.query(SiteDetail).filter(
                and_(
                    SiteDetail.site_id == site.id,
                    SiteDetail.siterelease.like('2%')
                )
            ).order_by(SiteDetail.timemodified.desc()).first()

            if details is not None:
                moodle_2_sites.append(site)

        # Generate the list of choices for the template
        courses_info = []
        sites_info = []

        listed_courses = []
        # Create the courses list
        for course in courses:
            if course.course_id not in listed_courses:
                if course.version:
                    courses_info.append(
                        (course.course_id, "%s - v%s" %
                         (course.course.name, course.version)))
                else:
                    courses_info.append(
                        (course.course_id, "%s" %
                         (course.course.name)))
                listed_courses.append(course.course_id)

        # Create the sites list
        for site in moodle_2_sites:
            sites_info.append((site.id, site.baseurl))

        form.course.choices = sorted(courses_info, key=lambda x: x[1])
        form.site.choices = sorted(sites_info, key=lambda x: x[1])
        form.filter.choices = [
            (folder, folder) for folder in get_course_folders(
                current_app.config['INSTALL_COURSE_FILE_PATH']
            )
        ]

        return render_template('install_course.html',
                               form=form, user=current_user)

    elif request.method == 'POST':
        # Course installation results
        output = ''

        # An array of unicode strings will be passed, they need to be integers
        # for the query
        selected_courses = [int(cid) for cid in request.form.getlist('course')]
        site_ids = [site_id for site_id in request.form.getlist('site')]
        sites = [Site.query.filter_by(id=site).first() for site in site_ids]

        course_details = g.db_session.query(CourseDetail).filter(
            CourseDetail.course_id.in_(selected_courses)
        ).all()

        for site in sites:
            # The site to install the courses
            install_url = ("http://%s/webservice/rest/server.php?" +
                           "wstoken=%s&wsfunction=%s") % (
                        site.baseurl,
                        site.get_token('orvsd_installcourse'),
                        current_app.config['INSTALL_COURSE_WS_FUNCTION'])
            install_url = str(install_url.encode('utf-8'))

            # Loop through the courses, generate the command to be run, run it,
            # and append the ouput to output
            #
            # Currently this will break as our db is not setup correctly yet
            for course_detail in course_details:
                res = install_course_to_site.delay(
                    course_detail.id, install_url
                )
                result_data = SiteCourse(site.id, course_detail.id, res.id)
                g.db_session.add(result_data)
                g.db_session.commit()

            output += (str(len(course_details)) + " course install(s) for " +
                       site.name + " started.\n")

        return render_template('install_course_output.html',
                               output=output,
                               user=current_user)


@mod.route("/courses/list/update", methods=['GET', 'POST'])
@requires_role('helpdesk')
@login_required
def update_courselist():
    """
    Updates the database to contain the most recent course
    and course detail entries, based on available files.
    """

    if request.method == "POST":
        base_path = current_app.config.get('INSTALL_COURSE_FILE_PATH', None)
        if base_path and os.path.exists(base_path):
            update_courselist_task.delay()
        else:
            flash("Invalid INSTALL_COURSE_FILE_PATH in your config")

    return render_template('update_courses.html', user=current_user)


"""
School
"""


@mod.route("/schools/migrate")
def migrate_schools():
    """
    Returns a rendered template for moving schools from the unknown school
    district to other districts.
    """

    # Unknown district to get schools from
    unknown = District.query.filter_by(name='z No district found').first()
    schools = School.query.filter_by(district_id=unknown.id).all()

    # List of Districts to move the unknuwns
    all_districts = District.query.all()
    all_districts.sort(key=lambda d: d.name)

    return render_template(
        "migrate.html",
        districts=all_districts,
        schools=schools, user=current_user
    )


@mod.route("/schools/<id>/view")
@requires_role('helpdesk')
@login_required
def view_schools(id):
    """
    Returns and renders a template with a list of sites for a given school.
    """

    # This should be an editable field on the template that modifies which
    # courses are shown via js.
    min_users = 1

    school = School.query.filter_by(id=id).first()
    # School license usually defaults to ''.
    school.license = school.license or None

    # Keep them separated for organizational/display purposes
    moodle_sites = g.db_session.query(Site).filter(and_(
        Site.school_id == id,
        Site.sitetype == 'moodle')).all()

    drupal_sites = g.db_session.query(Site).filter(and_(
        Site.school_id == id,
        Site.sitetype == 'drupal')).all()

    if moodle_sites or drupal_sites:
        moodle_sitedetails = []
        for site in moodle_sites:
            site_detail = SiteDetail.query.filter_by(site_id=site.id) \
                .order_by(SiteDetail
                          .timemodified
                          .desc()) \
                .first()

            if site_detail:
                site_detail.adminlist = json.loads(site_detail.adminlist)
                # Filter courses to display based on num of users.
                if site_detail.courses:
                    site_detail.courses = filter(
                        lambda x: x['enrolled'] > min_users,
                        json.loads(site_detail.courses)
                    )
            moodle_sitedetails.append(site_detail)

        moodle_siteinfo = zip(moodle_sites, moodle_sitedetails)

        drupal_sitedetails = []
        for site in drupal_sites:
            site_detail = SiteDetail.query.filter_by(site_id=site.id) \
                .order_by(SiteDetail
                          .timemodified
                          .desc()) \
                .first()

            if site_detail:
                site_detail.adminlist = json.loads(site_detail.adminlist)
                drupal_sitedetails.append(site_detail)

        drupal_siteinfo = zip(drupal_sites, drupal_sitedetails)

        return render_template("school.html", school=school,
                               moodle_siteinfo=moodle_siteinfo,
                               drupal_siteinfo=drupal_siteinfo,
                               user=current_user)
    else:
        return render_template("school_data_notfound.html", school=school,
                               user=current_user)
