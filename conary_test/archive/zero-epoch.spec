Name: zero-epoch
Epoch: 0
Version: 0.1
Release: 1
Group: Dummy
License: GPL
#Source: %{name}-%{version}.tar.gz
BuildRoot: /var/tmp/%{name}-%{version}-root
Summary: RPM with zero epoch

%description
Description

%prep
%setup -c -T

%build

%install
rm -rf $RPM_BUILD_ROOT
install -d $RPM_BUILD_ROOT

# A regular file
install -d $RPM_BUILD_ROOT/%{_datadir}
cat > $RPM_BUILD_ROOT/%{_datadir}/%{name}.txt << EOF
Some data
EOF

%clean
rm -rf $RPM_BUILD_ROOT

%files
%defattr(0644,root,root)
%{_datadir}/%{name}.txt
