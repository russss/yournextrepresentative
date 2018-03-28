from braces.views import LoginRequiredMixin
from django import forms as django_forms
from django.core.urlresolvers import reverse
from django.db import transaction
from django.http import HttpResponseRedirect
from django.views.generic import FormView, TemplateView
from popolo.models import Identifier

from bulk_adding import forms, helpers
from candidates.models import MembershipExtra, OrganizationExtra, PersonExtra
from elections.models import Election

# Assume 5 winners if we have no other info.
# If someone reports this, we can update EE and re-import
WINNER_COUNT_IF_NONE = 5


class BasePartyBulkAddView(LoginRequiredMixin, TemplateView):
    def get_election(self):
        return Election.objects.get(slug=self.kwargs['election'])

    def get_party(self):
        identifier = Identifier.objects.get(
            scheme='electoral-commission',
            identifier=self.kwargs['party_id']
        )
        return identifier.content_object

    def get_pee_qs(self, election):
        qs = election.postextraelection_set.all()
        qs = qs.order_by('postextra__base__label')
        return qs


class SelectPartyForm(BasePartyBulkAddView, FormView):
    template_name = "bulk_add/parties/select_party.html"
    form_class = forms.SelectPartyForm

    def get_form_kwargs(self):
        kwargs = super(SelectPartyForm, self).get_form_kwargs()
        kwargs['election'] = self.get_election()
        return kwargs

    def form_invalid(self, form):
        form_data = dict(form.data)
        for k, v in form_data.items():
            if not k == "csrfmiddlewaretoken":
                del form_data[k]
            form.data = form_data
        return self.render_to_response(self.get_context_data(form=form))

    def form_valid(self, form):
        org = OrganizationExtra.objects.get(pk=form.cleaned_data['party'])
        org_ec_id = org.base.identifiers.get(scheme='electoral-commission')
        return HttpResponseRedirect(
            reverse('bulk_add_by_party', kwargs={
                'election': self.get_election().slug,
                'party_id': org_ec_id.identifier,
            })
        )


class BulkAddPartyView(BasePartyBulkAddView):
    template_name = "bulk_add/parties/add_form.html"

    def get_context_data(self, **kwargs):
        context = super(BulkAddPartyView, self).get_context_data(**kwargs)
        context['election_obj'] = self.get_election()
        context['party'] = self.get_party()
        context['form'] = forms.AddByPartyForm()
        posts = []
        qs = self.get_pee_qs(context['election_obj'])
        for pee in qs:
            factory = django_forms.formset_factory(
                forms.NameOnlyPersonForm,
                extra=pee.winner_count or WINNER_COUNT_IF_NONE,
                formset=forms.BulkAddByPartyFormset
            )
            existing = MembershipExtra.objects.filter(
                election=pee.election,
                base__post=pee.postextra.base,
                base__on_behalf_of=context['party'],
            )
            post_info = {
                'pee': pee,
                'existing': existing,
                'formset': factory(prefix=pee.pk),
            }
            posts.append(post_info)

        context['posts'] = posts

        return context

    def post(self, *args, **kwargs):
        qs = self.get_pee_qs(self.get_election())
        session_data = {
            'source': self.request.POST.get('source'),
            'post_data': []
        }
        for pee in qs:
            factory = django_forms.formset_factory(
                forms.NameOnlyPersonForm,
                extra=pee.winner_count or WINNER_COUNT_IF_NONE,
                formset=forms.BulkAddByPartyFormset
            )
            formset = factory(self.request.POST, prefix=pee.pk)
            session_data['post_data'].append({
                'pee_pk': pee.pk,
                'data': formset.cleaned_data,
            })
        self.request.session['bulk_add_by_party_data'] = session_data
        self.request.session.save()

        return HttpResponseRedirect(
            reverse('bulk_add_by_party_review', kwargs={
                'election': self.kwargs['election'],
                'party_id': self.kwargs['party_id'],
            })
        )


class BulkAddPartyReviewView(BasePartyBulkAddView):
    template_name = "bulk_add/parties/add_review_form.html"

    def get_context_data(self, **kwargs):
        context = super(
            BulkAddPartyReviewView, self).get_context_data(**kwargs)

        post_data = self.request.session['bulk_add_by_party_data'].get(
            'post_data'
        )
        election = self.get_election()
        party = self.get_party()
        formsets = []
        for post in post_data:
            if not any(post['data']):
                continue

            pee = election.postextraelection_set.get(pk=post['pee_pk'])

            formset = forms.BulkAddReviewNameOnlyFormSet(
                initial=post['data'], prefix=pee.pk)
            formsets.append({
                'pee': pee,
                'data': formset,
                'management_form': formset.management_form
            })

        context['formsets'] = formsets
        context['election_obj'] = election
        context['party'] = party
        return context

    def post(self, request, *args, **kwargs):
        qs = self.get_pee_qs(self.get_election())
        formsets = []
        pee_ids = set([
            int(k.split('-')[0])
            for k in request.POST.keys()
            if k.endswith('INITIAL_FORMS')
        ])
        for pee in qs.filter(pk__in=pee_ids):
            factory = django_forms.formset_factory(
                forms.NameOnlyPersonForm,
                extra=pee.winner_count or WINNER_COUNT_IF_NONE,
                formset=forms.BulkAddReviewNameOnlyFormSet
            )
            formset = factory(self.request.POST, prefix=pee.pk)
            formset.pee = pee

            formsets.append(formset)

        if all([f.is_valid() for f in formsets]):
            return self.form_valid(formsets)
        else:
            return self.form_invalid(formsets)

    def form_valid(self, formsets):
        source = self.request.session['bulk_add_by_party_data'].get('source')
        with transaction.atomic():
            for formset in formsets:
                for person_form in formset:
                    data = person_form.cleaned_data
                    data['source'] = source
                    if data.get('select_person') == "_new":
                        # Add a new person
                        person_extra = helpers.add_person(self.request, data)
                    else:
                        person_extra = PersonExtra.objects.get(
                            base__pk=int(data['select_person']))
                    helpers.update_person(
                        self.request,
                        person_extra,
                        self.get_party(),
                        formset.pee,
                        source
                    )

        url = reverse('lookup-postcode')
        return HttpResponseRedirect(url)

    def form_invalid(self, formsets):
        context = self.get_context_data()
        return self.render_to_response(context)