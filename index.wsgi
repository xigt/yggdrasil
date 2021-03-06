import json
import logging
import os
import sys

# -------------------------------------------
# Set up the Flask app
# -------------------------------------------
import re
from collections import OrderedDict

from flask import Flask, render_template, url_for, request, make_response, Response, abort

from typing import List, Tuple

app = Flask(__name__)
application = app

# -------------------------------------------
# Import the configuration.
# -------------------------------------------
sys.path.append(os.path.dirname(__file__))
from yggdrasil import config

# -------------------------------------------
# Add the configuration to the app config
# -------------------------------------------
app.config.from_object(config)

# -------------------------------------------
# Get the other constants, config vars
# -------------------------------------------
from yggdrasil.config import PYTHONPATH, LINE_TAGS, LINE_ATTRS, XIGTVIZ, PDF_DIR
from yggdrasil.consts import NORM_STATE, CLEAN_STATE, RAW_STATE, NORMAL_TABLE_TYPE, CLEAN_TABLE_TYPE, HIDDEN
from yggdrasil.utils import aln_to_json


# -------------------------------------------
# Add the additional path items.
# -------------------------------------------
for path_item in PYTHONPATH.split(':'):
    sys.path.append(path_item)

from yggdrasil.metadata import get_rating, set_rating, set_comment, get_comment, get_reason
from yggdrasil.users import get_user_corpora, get_state, set_state, list_users
from yggdrasil.igt_operations import replace_lines, add_editor_metadata, add_split_metadata, add_raw_tier, \
    add_clean_tier, add_normal_tier, columnar_align_l_g

# -------------------------------------------
# ODIN_UTILS IMPORTS
# -------------------------------------------
import odinclean, odinnormalize

# -------------------------------------------
# XIGT Imports
# -------------------------------------------
from xigt import Igt, Item, Tier, XigtCorpus
from xigt.codecs import xigtjson, xigtxml
import xigt.xigtpath



# -------------------------------------------
# Now that we've imported our dependencies,
# we can register the blueprint.
# -------------------------------------------
from sleipnir import dbi

# -------------------------------------------
# Import other stuff here.
# -------------------------------------------

from intent.consts import CLEAN_ID, NORM_ID, all_punc_re_mult
from intent.igt.igtutils import is_strict_columnar_alignment, rgp, rgencode
from intent.igt.create_tiers import lang_lines, gloss_line, trans_lines, \
    generate_lang_words, generate_gloss_words, generate_trans_words, lang, gloss, morphemes, glosses, trans, \
    pos_tag_tier, has_lang, has_morphemes, has_glosses, has_gloss, has_trans, gloss_tag_tier, trans_tag_tier
from intent.igt.igt_functions import copy_xigt, delete_tier, heur_align_inst, classify_gloss_pos, \
    tag_trans_pos, project_gloss_pos_to_lang, add_gloss_lang_alignments, get_bilingual_alignment, \
    get_trans_lang_alignment, get_trans_gloss_alignment, get_trans_glosses_alignment, find_lang_word, find_gloss_word, \
    x_contains_y, get_glosses_morphs_alignment, add_pos_tags, set_bilingual_alignment
from intent.igt.references import raw_tier, cleaned_tier, normalized_tier, item_index, xigt_find
from intent.igt.exceptions import NoNormLineException, NoGlossLineException, GlossLangAlignException, \
    NoLangLineException, NoTransLineException
from intent.utils.listutils import flatten_list
from intent.igt.metadata import get_intent_method

app.debug = True

# -------------------------------------------
# Set up logging.
# -------------------------------------------
logging.basicConfig(level=logging.DEBUG)
YGG_LOG = logging.getLogger('YGG')

# -------------------------------------------
# Set up XigtViz settings
# -------------------------------------------
xv_conf = os.path.join(XIGTVIZ, 'config.json')
with open(xv_conf) as f:
    xv_settings = json.loads(f.read())
    app.config['xvs'] = xv_settings

# -------------------------------------------
# The default route. Display the browser window
# to the user.
# -------------------------------------------
@app.route('/')
def main():
    return render_template('login_screen.html', try_again=False)

# -------------------------------------------
# Retrieve the specific user.
# -------------------------------------------
@app.route('/user/<userid>')
def get_user(userid):
    """
    This is the equivalent of the login function.
    """
    user_corpora = get_user_corpora(userid)

    # DEBUG Output
    YGG_LOG.debug('Attempting to log in user "{}"'.format(userid))
    YGG_LOG.debug('Corpora available to user "{}": {}'.format(userid,
                                                              user_corpora))

    if user_corpora is not None:
        all_corpora = dbi.list_corpora()
        filtered_corpora = [c for c in all_corpora if c.get('id') in user_corpora]
        sorted_corpora = sorted(filtered_corpora,
                                key=lambda x: x.get('name'))
        return render_template('browser.html', corpora=sorted_corpora, user_id=userid)
    else:
        YGG_LOG.error('User "{}" has no available corpora.'.format(userid))
        return render_template('login_screen.html', try_again=True)

# -------------------------------------------
# Download the corpus in xml.
# -------------------------------------------
@app.route('/download/<corp_id>')
def download_corp(corp_id):
    xc = dbi.get_corpus(corp_id) #
    # type: XigtCorpus
    xc.sort(key=igt_id_sort)
    r = Response(xigtxml.dumps(xc), mimetype='text/xml')
    r.headers['Content-Disposition'] = "attachment; filename={}.xml".format(dbi._get_name(corp_id))
    return r


def igt_id_sort(igt):
    id_str = igt.id
    igt_re = re.search('([0-9]+)-([0-9]+)', id_str)
    if igt_re:
        igt_id, inst_id = igt_re.groups()
        return (int(igt_id), int(inst_id))
    else:
        igt_re=re.search('([0-9]+)', id_str)
        if igt_re:
            return int(igt_re.group(1))
        else:
            return id_str

# -------------------------------------------
# When a user clicks a "corpus", display the
# IGT instances contained by that corpus below.
# -------------------------------------------
@app.route('/populate/<corp_id>', methods=['POST'])
def populate(corp_id):
    YGG_LOG.debug('Attempting to fetch corpus id "{}"'.format(corp_id))
    xc = dbi.get_corpus(corp_id)
    xc.sort(key=igt_id_sort)

    data = json.loads(request.get_data().decode())
    user_id = data.get('userID')

    filtered_list = []

    ratings = {inst.id: get_rating(inst) for inst in xc}
    nexts = {}
    for i, igt in enumerate(xc):

        # Skip this instance if we've hidden it
        # (As we do for duplicates)
        if get_state(user_id, corp_id, igt.id) != HIDDEN:
            filtered_list.append(igt)

        if i < len(xc)-1:
            nexts[igt.id] = xc[i+1].id
        else:
            nexts[igt.id] = None


    # filtered_list = sorted(filtered_list, key=igt_id_sort)


    return render_template('igt_list.html', igts=filtered_list, corp_id=corp_id, ratings=ratings, nexts=nexts)

# -------------------------------------------
# When a user clicks an IGT instance, display
# the instances and the editor portions in the
# main display panel.
# -------------------------------------------
@app.route('/display/<corp_id>/<igt_id>', methods=['GET'])
def display(corp_id, igt_id):

    # -------------------------------------------
    # Start by getting the IGT instance.
    # -------------------------------------------
    inst = dbi.get_igt(corp_id, igt_id) # type: Igt

    # -------------------------------------------
    # Get the state from the user file.
    # -------------------------------------------
    state = get_state(request.args.get('user'), corp_id, igt_id)
    if state is None:
        state = RAW_STATE

    rt = raw_tier(inst)
    ct, nt = None, None
    if state >= CLEAN_STATE:
        ct = cleaned_tier(inst)

    if state == NORM_STATE:
        nt = normalized_tier(inst)

    nt_content = None
    if nt is not None:
        state = NORM_STATE
        nt_content = render_template("tier_table.html", tier=nt, table_type=NORMAL_TABLE_TYPE, id_prefix=NORM_ID, editable=True)

    docid = inst.attributes.get('doc-id')
    pdflink = None
    if pdfpath(docid):
        pdflink='/pdf/'+docid

    # -------------------------------------------
    # Render the element template.
    # -------------------------------------------
    content = render_template('element.html', state=state, rt=rt, ct=ct, nt_content=nt_content,
                              igt=inst, igt_id=igt_id, corp_id=corp_id,
                              comment=get_comment(inst), rating=get_rating(inst), reason=get_reason(inst),
                              pdflink=pdflink,
                              lang=xigt.xigtpath.find(inst, './/dc:subject').text)
    response_data = {'content':content}
    # -------------------------------------------
    # If the instance already has group2 info,
    # -------------------------------------------
    gw_pos = xigt_find(inst, id='gw-pos')
    if gw_pos and get_intent_method(gw_pos) == 'editor':
        response_data['group2'] = display_group_2(inst)

    return json.dumps(response_data)

def pdfpath(docid):
    path = os.path.join(PDF_DIR, '{}.pdf'.format(docid))
    if docid and os.path.exists(path):
        return path
    else:
        return None

@app.route('/pdf/<docid>', methods=['GET'])
def get_pdf(docid):
    pdfpath = os.path.join(PDF_DIR, '{}.pdf'.format(docid))
    if os.path.exists(pdfpath):
        with open(pdfpath, 'rb') as f:
            r = Response(f.read(), mimetype='application/pdf')
            r.headers['Content-Disposition'] = "attachment; filename={}.pdf".format(docid)
            return r
    else:
        abort(404)

# -------------------------------------------
# After the user has corrected the clean tier
# as necessary, auto-generate the normalized
# tier.
# -------------------------------------------
@app.route('/normalize/<corp_id>/<igt_id>', methods=['POST'])
def normalize(corp_id, igt_id):

    # Get the data...
    data = request.get_json()
    clean_lines = data.get('lines')

    # Retrieve the original IGT from the database
    # and swap out the new clean lines for the old
    # clean lines, then generate a new normalized
    # tier based on that.
    inst = dbi.get_igt(corp_id, igt_id)
    replace_lines(inst, clean_lines, None)

    # Now, remove any old normalized tier and replace it with
    # one generated by the odin-utils script.
    if normalized_tier(inst) is not None:
        delete_tier(normalized_tier(inst))

    odinnormalize.add_normalized_tier(inst, cleaned_tier(inst))
    nt = normalized_tier(inst)

    content = render_template("tier_table.html", tier=nt, table_type=NORMAL_TABLE_TYPE, id_prefix=NORM_ID, editable=True)

    retdata = {"content":content}

    return json.dumps(retdata)

def tagfunc(tagstr):
    tags = tagstr.split('+')
    maintag = tags[0]
    r = {'maintag':maintag, 'attrs':[]}
    for elt in tags[1:]:
        r['attrs'].append(elt)
    return r

# -------------------------------------------
# Generate a cleaned tier
# -------------------------------------------
@app.route('/clean/<corp_id>/<igt_id>', methods=['GET'])
def clean(corp_id, igt_id):

    inst = dbi.get_igt(corp_id, igt_id)

    # Remove the previously cleaned tier, and
    # regenerate it with the odin-utils script.
    if cleaned_tier(inst):
        delete_tier(cleaned_tier(inst))

    odinclean.add_cleaned_tier(inst, raw_tier(inst))
    ct = cleaned_tier(inst)

    return render_template("tier_table.html",
                           table_type=CLEAN_TABLE_TYPE,
                           tier=ct,
                           id_prefix=CLEAN_ID,
                           editable=True,
                           tag_options=LINE_TAGS,
                           label_options=LINE_ATTRS,
                           tagfunc=tagfunc)

def get_lines(inst: Igt):
    try:
        ll = lang_lines(inst)[0]
    except NoNormLineException:
        ll = None

    try:
        gl = gloss_line(inst)
    except (NoNormLineException, NoGlossLineException):
        gl = None

    try:
        tl = trans_lines(inst)[0]
    except NoNormLineException:
        tl = None

    return ll, gl, tl

def clean_tokenization(inst: Igt):

    for tier in [xigt_find(inst, id=id_) for id_ in ['tw', 'gw', 'g', 'm', 'w', 't', 'p']]:
        if tier is not None:
            inst.remove(tier)


def tokenize(inst: Igt):
    ll, gl, tl = get_lines(inst)

    # Start by creating words:
    if tl is not None: generate_trans_words(inst)
    if ll is not None: generate_lang_words(inst)
    if gl is not None:
        generate_gloss_words(inst)
        glosses(inst)

# -------------------------------------------
# After the user has corrected the normalized tiers,
# analyze them and provide feedback.
# -------------------------------------------
@app.route('/intentify/<corp_id>/<igt_id>', methods=['POST'])
def intentify(corp_id, igt_id):

    data = request.get_json()

    inst = Igt(id=igt_id)
    add_raw_tier(inst, data.get('raw'))
    add_clean_tier(inst, data.get('clean'))
    add_normal_tier(inst, data.get('normal'))

    ll, gl, tl = get_lines(inst)

    response = {}

    # Check that the tiers are of equal length...
    def equal_lengths(tier_a, tier_b):
        tier_a_items = [i for i in tier_a if not re.match('^{}$'.format(all_punc_re_mult), i)]
        tier_b_items = [i for i in tier_b if not re.match('^{}$'.format(all_punc_re_mult), i)]
        return len(tier_a_items) == len(tier_b_items)


    # Check that the language line and gloss line
    # have the same number of whitespace-delineated tokens.
    if ll is not None and gl is not None:
        response['glw'] = 1 if equal_lengths([l.value() for l in lang(inst)], [g.value() for g in gloss(inst)]) else 0

        # Check that the language line and gloss line
        # have the same number of morphemes.
        morph_list = flatten_list([re.split('[\-=]', w.value()) for w in lang(inst)])
        gloss_list = flatten_list([re.split('[\-=]', g.value()) for g in gloss(inst)])
        response['glm'] = 1 if equal_lengths(morph_list, gloss_list) else 0
    else:
        response['glw'] = 0
        response['glm'] = 0

    # Check that the normalized tier has only L, G, T for tags.
    norm_tags = set([l.attributes.get('tag') for l in normalized_tier(inst)])
    response['tag'] = 1 if set(['L','G','T']).issubset(norm_tags) else 0

    # Check that the g/l lines are aligned in strict columns
    if ll is None or gl is None:
        response['col'] = 0
    else:
        response['col'] = 1 if is_strict_columnar_alignment(ll.value(), gl.value()) else 0

    # If the number of gloss words on the lang line and gloss
    # line do not match, abort further enrichment and provide
    # a warning.
    if response['glw'] == 0:
        response['group2'] = render_template('group2/group_2_invalid.html')
        return json.dumps(response)

    # -------------------------------------------
    # DO ENRICHMENT
    # -------------------------------------------

    # Start by creating words/morphs
    tokenize(inst)

    # Add POS tags as necessary
    if tl is not None: tag_trans_pos(inst)

    # Add alignment
    if gl is not None and tl is not None:
        heur_align_inst(inst)

        # Try POS tagging the language line...
        try:
            add_gloss_lang_alignments(inst)
        except GlossLangAlignException:
            pass

        if classify_gloss_pos(inst):
            try:
                project_gloss_pos_to_lang(inst)
            except GlossLangAlignException:
                pass

    inst.sort_tiers()

    response['group2'] = display_group_2(inst)

    return json.dumps(response)

class MorphMap(object):
    """
    Make passing the list of words and morphemes easier,
    as well as finding which word a morpheme belongs to
    """
    def __init__(self, inst):
        self.w_to_m = OrderedDict()
        self.m_to_w = OrderedDict()
        if has_gloss(inst): self._get_aln(inst, glosses(inst), gloss(inst))
        if has_lang(inst): self._get_aln(inst, morphemes(inst), lang(inst))

    def _get_aln(self, inst, morph_tier, word_tier):
        for m in morph_tier:
            self.m_to_w[m] = None
            for w in word_tier:
                if w not in self.w_to_m: self.w_to_m[w] = []
                if x_contains_y(inst, w, m):
                    self.w_to_m[w].append(m)
                    self.m_to_w[m] = w
                    # break # A morph can only be aligned with one word

    def morphs(self, m_type='g'):
        return [m for m in self.m_to_w.keys() if m.id.startswith(m_type)]
    def words(self, w_type='w'):
        return [w for w in self.w_to_m.keys() if w.id.startswith(w_type)]

    def lws(self): return self.words('w')
    def lms(self): return self.morphs('m')
    def gws(self): return self.words('gw')
    def gms(self): return self.morphs('g')
    def get_ms(self, w): return self.w_to_m.get(w, [])
    def get_mids(self, w): return [m.id for m in self.w_to_m.get(w)]
    def get_w(self, m): return self.m_to_w.get(m, None)


def display_group_2(inst):
    """
    This function is responsible for compiling all the elements of the "group 2"
    items for editing; e.g. POS tags and word alignment.

    It assumes all enrichment has already been done, and does not
    perform any modification.

    :type inst: Igt
    """
    return_html = ''


    # Obtain alignments between lang_w<->morphs, gloss_w<->glosses
    mm = MorphMap(inst)

    gloss_pos = gloss_tag_tier(inst)

    trans_w = trans(inst) if has_trans(inst) else []
    trans_pos = trans_tag_tier(inst)

    tw_gm_aln = get_trans_glosses_alignment(inst)
    if not tw_gm_aln:
        tw_gm_aln = []
    gm_lm_aln = get_glosses_morphs_alignment(inst)


    return_html += render_template('group2/group_2.html',
                                   morph_map=mm,
                                   trans_w=trans_w,
                                   gloss_pos=gloss_pos,
                                   trans_pos=trans_pos,
                                   tw_gm_aln=tw_gm_aln,
                                   gm_lm_aln=gm_lm_aln
                                   )

    return return_html


# -------------------------------------------
# Save a file after changes
# -------------------------------------------
@app.route('/save/<corp_id>/<igt_id>', methods=['PUT'])
def save(corp_id, igt_id):
    data = request.get_json()

    # -------------------------------------------
    # Get the lines
    # -------------------------------------------
    clean = data.get('clean')
    norm  = data.get('norm')
    user_id = data.get('userID')

    # Get the other data
    rating = data.get('rating')
    reason = data.get('reason', '')
    comment = data.get('comment', '')

    # Set the rating...
    if norm:
        set_state(user_id, corp_id, igt_id, NORM_STATE)
    elif clean:
        set_state(user_id, corp_id, igt_id, CLEAN_STATE)
    else:
        set_state(user_id, corp_id, igt_id, RAW_STATE)





    # Retrieve the IGT instance, and swap in the
    # new cleaned and normalized tiers.
    igt = dbi.get_igt(corp_id, igt_id) # type: Igt
    igt = replace_lines(igt, clean, norm)
    set_rating(igt, user_id, rating, reason)


    # -------------------------------------------
    # Now, the group 2 stuff.
    #
    # If no POS tags are present, skip tokenizing
    # add adding the other stuff.
    # -------------------------------------------
    gw_pos = data.get('gw_pos')
    tw_pos = data.get('tw_pos')

    clean_tokenization(igt)  # Clean previous tokenization

    if gw_pos or tw_pos:
        # Ensure that glosses, morphs, words, etc
        # are present (generated from the norm tier)
        tokenize(igt)

        # Save alignments
        tw_gm_aln = data.get('tw_gm_aln')
        if tw_gm_aln:
            YGG_LOG.debug("Edited alignment data present")
            YGG_LOG.debug(tw_gm_aln)
            tw_tier = xigt_find(igt, id='tw') # type: Tier
            g_tier = xigt_find(igt, id='g') # type: Tier

            aln_indices = []
            for tw_id, g_id in tw_gm_aln:
                tw_index = item_index(xigt_find(tw_tier, id=tw_id))
                gm_index = item_index(xigt_find(g_tier, id=g_id))
                aln_indices.append((tw_index, gm_index))

            set_bilingual_alignment(igt, tw_tier, g_tier, aln_indices, 'editor')

        add_pos_tags(igt, 'gw', [pos for w_id, pos in gw_pos], tag_method='editor')
        add_pos_tags(igt, 'tw', [pos for w_id, pos in tw_pos], tag_method='editor')



    # -------------------------------------------
    # Finally, add comments and metadata
    # -------------------------------------------

    # Only add the comment if it is contentful.
    if comment.strip():
        set_comment(igt, user_id, comment)

    # Add the data provenance to the tier.
    add_editor_metadata(igt)

    # Do the actually saving of the igt instance.
    dbi.set_igt(corp_id, igt_id, igt)

    return make_response()

# -------------------------------------------
# Split an instance
# -------------------------------------------
@app.route('/split/<corp_id>/<igt_id>', methods=['POST'])
def split(corp_id, igt_id):
    data = request.get_json()

    user_id = data.get('userID')
    clean   = data.get('clean')
    norm    = data.get('norm')

    # -------------------------------------------
    # Get the original instance that we're going
    # to split, and add some metadata info.
    # -------------------------------------------
    igt = dbi.get_igt(corp_id, igt_id)
    igt = replace_lines(igt, clean, norm)
    add_editor_metadata(igt)
    add_split_metadata(igt, igt.id)

    # -------------------------------------------
    # Make our new copies.
    # -------------------------------------------
    igt_a = copy_xigt(igt)
    igt_b = copy_xigt(igt)

    igt_a.id = igt.id+'_a'
    igt_b.id = igt.id+'_b'

    dbi.add_igt(corp_id, igt_a)
    dbi.add_igt(corp_id, igt_b)

    set_state(user_id, corp_id, igt.id, HIDDEN)
    set_state(user_id, corp_id, igt_a.id, NORM_STATE)
    set_state(user_id, corp_id, igt_b.id, NORM_STATE)

    return json.dumps({'next':igt_a.id})

@app.route('/delete/<corp_id>/<igt_id>', methods=['POST'])
def delete_igt(corp_id, igt_id):

    user = request.get_json().get('userID')

    set_state(user, corp_id, igt_id, HIDDEN)
    dbi.del_igt(corp_id, igt_id)

    return make_response()

# -------------------------------------------
# Static files
# -------------------------------------------

@app.route('/static/<path:path>', methods=['GET'])
def default(path):
    return url_for('static', filename=path)