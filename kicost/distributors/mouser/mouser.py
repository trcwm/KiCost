# MIT license
#
# Copyright (C) 2015 by XESS Corporation
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

# Inserted by Pasteurize tool.
from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division
from __future__ import absolute_import
from builtins import zip
from builtins import range
from builtins import int
from builtins import str
from future import standard_library
standard_library.install_aliases()

import future

import re
import difflib
from bs4 import BeautifulSoup
import http.client # For web scraping exceptions.
from .. import urlquote, urlsplit, urlunsplit, urlopen, Request
from .. import WEB_SCRAPE_EXCEPTIONS
from .. import FakeBrowser
from ...globals import PartHtmlError
from ...globals import logger, DEBUG_OVERVIEW, DEBUG_DETAILED, DEBUG_OBSESSIVE

from .. import distributor_dict

def define_locale_currency(locale_iso=None, currency_iso=None):
    '''@brief Configure the distributor for the country and currency intended.
    
    Scrape the configuration page and define the base URL of Mouser for the
    currency and locale chosen.
    The currency is predominant over the locale/country and the default are
    currency='USD' and locale='US' for Mouser.
    
    @param locale_iso `str` Country in ISO3166 alpha 2 standard.
    @param currency_iso `str` Currency in ISO4217 alpha 3 standard.'''
    
    # for now, just hard code the regional sites based on location only
    region_options = {
        "US" : ["USD", 'https://www.mouser.com'],
        "NL" : ["EUR", 'https://nl.mouser.com'],
        "UK" : ["EUR", 'https://ww.mouser.co.uk'],
        "DE" : ["GBP", 'https://www.mouser.de']
    }

    # check if the requested country is in the dictionary
    # if not, we check if there requested currency is EUR
    # and redirect to eu.mouser.com if this is the case.
    baseurl  = 'https://www.mouser.com'
    currency = 'USD'
    locale   = 'US'
    if not locale_iso in region_options:
        logger.warning('\tLocale not supported for Mouser')
        if currency_iso == 'EUR':
            baseurl  = 'https://eu.mouser.com'
            currency = 'EUR'
            locale   = 'EU'
            logger.warning('\tSwitching to European site for Mouser')
        else:
            logger.warning('\tSwitching to US site for Mouser')
    else:
        currency = region_options[locale_iso][0]
        baseurl  = region_options[locale_iso][1]
        locale   = locale_iso
            
    distributor_dict['mouser']['site']['url'] = baseurl
    distributor_dict['mouser']['site']['currency'] = currency  #pycountry.currencies.get(numeric=country.numeric).alpha_3
    distributor_dict['mouser']['site']['locale'] = locale
    logger.log(DEBUG_OBSESSIVE, 'Setting Mouser currency/locale to: %s %s', currency, locale)
    return

def get_price_tiers(html_tree):
    '''@brief Get the pricing tiers from the parsed tree of the Mouser product page.
       @param html_tree `str()` html of the distributor part page.
       @return `dict()` price breaks, the keys are the quantities breaks.
    '''
    price_tiers = {}
    try:
        pricing_tbl_tree = html_tree.find('div', class_='pdp-pricing-table')
        price_row_trees = pricing_tbl_tree.find_all('div', class_='div-table-row')
        for row_tree in price_row_trees:
            qty_tree, unit_price_tree, _ = row_tree.find('div', class_='row').find_all('div', class_='col-xs-4')
            try:
                qty = int(re.sub('[^0-9]', '', qty_tree.text))
                # FIXME: quick hack to deal with currency localization
                # in EUR countries, the decimal point is the',' character.
                if (distributor_dict['mouser']['site']['currency'] == 'EUR'):
                    unit_price_str = re.sub('[^0-9,]', '', unit_price_tree.text)
                    unit_price_str = re.sub(',','.', unit_price_str)
                    unit_price = float(unit_price_str)
                else:
                    unit_price = float(re.sub('[^0-9.]', '', unit_price_tree.text))
                price_tiers[qty] = unit_price
            except ValueError:
                pass # In case of "quote price", ignore and pass to next (check pn STM32F411RCT6).
        return price_tiers

        qty_strs = []
        for qty in html_tree.find('div',
                                  class_='PriceBreaks').find_all(
                                      'div',
                                      class_='PriceBreakQuantity'):
            qty_strs.append(qty.text)
        price_strs = []
        for price in html_tree.find('div',
                                    class_='PriceBreaks').find_all(
                                        'div',
                                        class_='PriceBreakPrice'):
            price_strs.append(price.text)
        qtys_prices = list(zip(qty_strs, price_strs))
        for qty_str, price_str in qtys_prices:
            try:
                qty = re.search('(\s*)([0-9,]+)', qty_str).group(2)
                qty = int(re.sub('[^0-9]', '', qty))
                price_tiers[qty] = float(re.sub('[^0-9\.]', '', price_str))
            except (TypeError, AttributeError, ValueError, IndexError):
                continue
    except AttributeError:
        # This happens when no pricing info is found in the tree.
        logger.log(DEBUG_OBSESSIVE, 'No Mouser pricing information found!')
        return price_tiers  # Return empty price tiers.
    return price_tiers


def get_part_num(html_tree):
    '''@brief Get the part number from the Mouser product page.
       @param html_tree `str()` html of the distributor part page.
       @return `list()`of the parts that match.
    '''
    try:
        partnum = html_tree.find(
                        'span', id='spnMouserPartNumFormattedForProdInfo'
                        ).text
        return partnum.strip()
    except AttributeError:
        logger.log(DEBUG_OBSESSIVE, 'No Mouser part number found!')
        return ''


def get_qty_avail(html_tree):
    '''@brief Get the available quantity of the part from the Mouser product page.
       @param html_tree `str()` html of the distributor part page.
       @return `int` avaliable quantity.
    '''
    try:
        qty_str = html_tree.find(
                                'div', class_='pdp-product-availability').find(
                                'div', class_='row').find(
                                'div', class_='col-xs-8').find('div').text
    except AttributeError as e:
        # No quantity found (not even 0) so this is probably a non-stocked part.
        # Return None so the part won't show in the spreadsheet for this dist.
        logger.log(DEBUG_OBSESSIVE, 'No Mouser part quantity found!')
        return None
    try:
        # quanitities will contain a '.' as the 1000 marker in EU
        # or ',' in the US and some other countries
        # quanitities are never fractional, so it is safe here
        # to ignore both ',' and '.'
        qty_str = re.search('(\s*)([0-9,.]*)', qty_str, re.IGNORECASE).group(2)
        return int(re.sub('[^0-9]', '', qty_str))
    except ValueError:
        # No quantity found (not even 0) so this is probably a non-stocked part.
        # Return None so the part won't show in the spreadsheet for this dist.
        logger.log(DEBUG_OBSESSIVE, 'No Mouser part quantity found!')
        return None


def get_part_html_tree(dist, pn, extra_search_terms='', url=None, descend=2, local_part_html=None, scrape_retries=2):
    '''@brief Find the Mouser HTML page for a part number and return the URL and parse tree.
       @param dist
       @param pn Part number `str()`.
       @param extra_search_terms
       @param url
       @param descend
       @param local_part_html
       @param scrape_retries `int` Quantity of retries in case of fail.
       @return (html `str()` of the page, url)
    '''

    # Use the part number to lookup the part using the site search function, unless a starting url was given.
    if url is None:
        url = distributor_dict['mouser']['site']['url'] + '/Search/Refine.aspx?Keyword=' + urlquote(
            pn + ' ' + extra_search_terms,
            safe='')
    elif url[0] == '/':
        url = distributor_dict['mouser']['site']['url'] + url
    elif url.startswith('..'):
        url = distributor_dict['mouser']['site']['url'] + '/Search/' + url

    # Open the URL, read the HTML from it, and parse it into a tree structure.
    req = FakeBrowser(url)

    # do we actually need this cookie?
    #req.add_header('Cookie', 'preferences=ps=www2&pl=en-US&pc_www2=USDe')

    for _ in range(scrape_retries):
        try:
            response = urlopen(req)
            html = response.read()
            break
        except WEB_SCRAPE_EXCEPTIONS:
            logger.log(DEBUG_DETAILED,'Exception while web-scraping {} from {}'.format(pn, dist))
            pass
    else: # Couldn't get a good read from the website.
        logger.log(DEBUG_OBSESSIVE,'No HTML page for {} from {}'.format(pn, dist))
        raise PartHtmlError

    # Abort if the part number isn't in the HTML somewhere.
    # (Only use the numbers and letters to compare PN to HTML.)
    if re.sub('[\W_]','',str.lower(pn)) not in re.sub('[\W_]','',str.lower(str(html))):
        logger.log(DEBUG_OBSESSIVE,'No part number {} in HTML page from {}'.format(pn, dist))
        raise PartHtmlError
    
    try:
        tree = BeautifulSoup(html, 'lxml')
    except Exception:
        logger.log(DEBUG_OBSESSIVE,'No HTML tree for {} from {}'.format(pn, dist))
        raise PartHtmlError

    # If the tree contains the tag for a product page, then just return it.
    if tree.find('div', id='pdpPricingAvailability') is not None:
        return tree, url

    # If the tree is for a list of products, then examine the links to try to find the part number.
    if tree.find('div', id='searchResultsTbl') is not None:
        logger.log(DEBUG_OBSESSIVE,'Found product table for {} from {}'.format(pn, dist))
        if descend <= 0:
            logger.log(DEBUG_OBSESSIVE,'Passed descent limit for {} from {}'.format(pn, dist))
            raise PartHtmlError
        else:
            # Look for the table of products.
            products = tree.find(
                'table',
                class_='SearchResultsTable').find_all(
                    'tr',
                    class_=('SearchResultsRowOdd', 'SearchResultsRowEven'))

            # Extract the product links for the part numbers from the table.
            product_links = [p.find('div', class_='mfrDiv').a for p in products]

            # Extract all the part numbers from the text portion of the links.
            part_numbers = [l.text for l in product_links]

            # Look for the part number in the list that most closely matches the requested part number.
            match = difflib.get_close_matches(pn, part_numbers, 1, 0.0)[0]

            # Now look for the link that goes with the closest matching part number.
            for l in product_links:
                if l.text == match:
                    # Get the tree for the linked-to page and return that.
                    logger.log(DEBUG_OBSESSIVE,'Selecting {} from product table for {} from {}'.format(l.text, pn, dist))
                    return get_part_html_tree(dist, pn, extra_search_terms,
                                              url=l.get('href', ''),
                                              descend=descend-1,
                                              scrape_retries=scrape_retries)

    # I don't know what happened here, so give up.
    logger.log(DEBUG_OBSESSIVE,'Unknown error for {} from {}'.format(pn, dist))
    raise PartHtmlError
