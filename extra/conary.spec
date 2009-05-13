%define __python %{_bindir}/python2.4
%define python_sitelib %(%{__python} -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")

%if "%{?rhel}" == "3" || "%{?rhel}" == "4"
%define python python24
%else
%define python python
%endif

Summary: Conary is a distributed software management system for Linux distributions.
Name: conary
Version: 2.0.42
Release: 1%{?dist}
License: CPL
Group: System Environment/Base
URL: http://wiki.conary.com/
Source0: %{name}-%{version}.tar.bz2
BuildRoot: %{_tmppath}/%{name}-%{version}-%{release}-root
Requires: %{python} >= 2.4
Requires: sqlite >= 3.2.1
Requires: %{python}-elementtree, %{python}-crypto
BuildRequires: %{python}-devel >= 2.4
BuildRequires: sqlite-devel >= 3.2.1
BuildRequires: elfutils-libelf-devel
BuildRequires: %{python}-elementtree
BuildRequires: %{python}-kid, %{python}-setuptools, %{python}-crypto

%define pythonlib %{python_sitelib}/%{name}

%description
Conary replaces existing software package management solutions (such
as RPM and dpkg) with one designed to enable loose collaboration
across the Internet. Unlike traditional packages, Conary packages are
defined by components and files stored in networked repositories.

System administrators will find Conary's approach to package
management and software provisioning results in more intelligent
handling of software and system configuration than traditional
solutions. Software packagers will spend time building quality
packages instead of dealing with the inflexible and fragile package
control scripts, versioning issues, and source code build issues
associated with other packaging solutions.

%package repository
Summary: Support for setting up and running a conary repository
Group: System Environment/Daemons
Requires: %{name} = %{version}

%description repository
This package includes support needed for setting up and running a
conary repository.

%prep
%setup -q

%build
make libdir=%{_prefix}/lib

%install
rm -rf $RPM_BUILD_ROOT
make install libdir=%{_prefix}/lib DESTDIR=%{buildroot}
rm -f %{buildroot}/%{_bindir}/conary-debug
rm -f %{buildroot}/%{_bindir}/rpm2cpio
install -d -m 755 %{buildroot}/var/lib/conarydb

%clean
rm -rf $RPM_BUILD_ROOT

%files
%defattr(-,root,root,-)
%doc LICENSE NEWS
%dir %{_sysconfdir}/conary/arch
%dir %{_sysconfdir}/conary/site
%dir %{_sysconfdir}/conary/use
%dir %{_sysconfdir}/conary/recipeTemplates
%config %{_sysconfdir}/conary/arch/*
%config %{_sysconfdir}/conary/site/*
%config %{_sysconfdir}/conary/use/*
%config %{_sysconfdir}/conary/mirrors/*
%config %{_sysconfdir}/conary/components
%config %{_sysconfdir}/conary/recipeTemplates/*
%config(noreplace) %{_sysconfdir}/conary/macros
%config(noreplace) %{_sysconfdir}/conary/pubring.gpg
%config(noreplace) %{_sysconfdir}/conary/trustdb.gpg
%{_bindir}/conary
%{_bindir}/cvc
%{_bindir}/cvcdesc
%{_bindir}/ccs2tar
%{_bindir}/dbsh
%{pythonlib}/*.py*
%{pythonlib}/build
%{pythonlib}/conaryclient
%{pythonlib}/commitaction
%{pythonlib}/deps
%{pythonlib}/dbstore
%{pythonlib}/lib
%{pythonlib}/local
%{pythonlib}/repository/*.py*
%exclude %{pythonlib}/repository/shimclient.py*
%{pythonlib}/_sqlite3.so
%{pythonlib}/sqlite3
%{_prefix}/lib/%{name}
%{_libexecdir}/%{name}
%{_datadir}/%{name}
%{_mandir}/man1/*.1*
%dir /var/lib/conarydb

%files repository
%defattr(-,root,root,-)
%{pythonlib}/repository/netrepos
%{pythonlib}/repository/shimclient.py*
%{pythonlib}/web
%{pythonlib}//server

%changelog
* Tue Sep 12 2006 Cristian Gafton <gafton@gmail.com> - 1.0.31-1
- add the /etc/conary/mirrors files

* Tue Jun 27 2006 Cristian Gafton <gafton@rpath.com> - 1.0.21-2
- update to handle python24 for RHELs and plain python for others

* Mon Jun 26 2006 Cristian Gafton <gafton@rpath.com> - 1.0.21-1
- initial build
